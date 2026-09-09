"""A 股工作流的本地 RSS/Atom/NewsNow 情报池（fail-open）。

负责从 RSS、Atom 与 NewsNow 等公开数据源抓取财经热点与新闻条目，
落地为内部情报条目（intelligence items），供个股分析报告与
"市场证据（market evidence）" 注入使用。

设计要点：
- 默认三个内置 NewsNow 模板（财联社热门、雪球热门股票、华尔街见闻快讯），
  便于一键启用"开箱即用"的题材数据源
- 对所有上游 URL 做白名单校验（禁止内网、禁止携带凭据、限制响应体积）
- 抓取、解析、入库解耦，任一环节失败都会把异常冒泡给调用方，
  但 ``fetch_enabled_sources`` 会按源聚合失败信息，保证单源失败不阻塞整体
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import threading
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from src.config import get_config
from src.repositories.intelligence_repo import IntelligenceRepository

_MAX_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 5
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
_AUTO_FETCH_MIN_INTERVAL_SECONDS = 60 * 60
_DNS_GUARD_LOCK = threading.Lock()
# 单源响应体积上限 2 MiB，避免恶意/异常大响应耗尽内存或拖慢抓取
_ALLOWED_TYPES = {"rss", "atom", "newsnow"}
# 默认开启的 NewsNow 数据源模板（名称 / source_id / 描述）
_DEFAULTS = (
    ("NewsNow 财联社热门", "cls-hot", "A 股盘面与题材热点"),
    ("NewsNow 雪球热门股票", "xueqiu-hotstock", "A 股个股讨论热度"),
    ("NewsNow 华尔街见闻快讯", "wallstreetcn-quick", "宏观与市场事件"),
)


class IntelligenceServiceError(ValueError):
    """情报源服务层面的业务错误（如 URL 非法、源未启用、源已存在等）。"""


class IntelligenceService:
    """情报源与情报条目的业务编排层。

    负责源（source）模板的生成、单源抓取、批量抓取、条目查询与"市场证据"提取。
    底层持久化由 `IntellienceRepository` 承担；HTTP 抓取、XML/JSON 解析由本类内部方法完成。
    """

    _auto_fetch_lock = threading.Lock()
    _auto_fetch_condition = threading.Condition(_auto_fetch_lock)
    _auto_fetch_in_progress = False
    _auto_fetch_last_run_at: Optional[datetime] = None
    _auto_fetch_last_result: Optional[Dict[str, Any]] = None

    def __init__(self, repository=None, config=None):
        """注入仓储与配置；默认使用 ``IntellienceRepository`` 与全局 :func:`get_config`。"""
        self.repo = repository or IntelligenceRepository()
        self.config = config or get_config()

    @classmethod
    def reset_auto_fetch_state(cls):
        """重置进程内的自动刷新协调状态，主要用于测试场景。"""
        with cls._auto_fetch_condition:
            cls._auto_fetch_in_progress = False
            cls._auto_fetch_last_run_at = None
            cls._auto_fetch_last_result = None
            cls._auto_fetch_condition.notify_all()

    def list_source_templates(self):
        """列出内置的 NewsNow 数据源模板，用于前端"一键添加"或后端引导。

        URL 通过 ``newsnow_base_url`` + ``/api/s?id=<source_id>`` 拼接而成，
        模板数量由 ``_DEFAULTS`` 决定。
        """
        base = str(self.config.newsnow_base_url).rstrip("/")
        items = [{"template_id": f"newsnow-{source_id}", "name": name, "source_type": "newsnow",
                  "url": f"{base}/api/s?id={source_id}", "scope_type": "market", "market": "cn", "description": description}
                 for name, source_id, description in _DEFAULTS]
        return {"items": items, "total": len(items)}

    def create_default_sources(self, *, enabled=False):
        """为每个内置模板创建（或跳过已存在的）数据源，返回每条模板的创建结果。"""
        result = []
        for template in self.list_source_templates()["items"]:
            existing = self.repo.get_source_by_name(template["name"])
            if existing:
                result.append({"created": False, "source": self._source(existing)})
                continue
            fields = dict(template); fields.pop("template_id"); fields["enabled"] = enabled
            result.append({"created": True, "source": self._source(self.repo.create_source(self._fields(fields)))})
        return {"items": result, "created_count": sum(1 for item in result if item["created"]), "total": len(result)}

    def create_source(self, payload: Dict[str, Any]):
        """创建一个新的情报源。

        校验字段合法性、URL 白名单，并保证 name 唯一；通过仓储落库后返回序列化结果。
        """
        fields = self._fields(payload); self._validate_url(fields["url"])
        if self.repo.get_source_by_name(fields["name"]): raise IntelligenceServiceError("intelligence source name already exists")
        return self._source(self.repo.create_source(fields))

    def list_sources(self, **filters):
        """分页查询情报源列表，支持按 ``enabled`` 过滤，固定 ``market="cn"``。"""
        rows, total = self.repo.list_sources(enabled=filters.get("enabled"), market="cn", page=filters.get("page", 1), page_size=filters.get("page_size", 50))
        return {"items": [self._source(row) for row in rows], "total": total, "page": filters.get("page", 1), "page_size": filters.get("page_size", 50)}

    def set_source_enabled(self, source_id: int, enabled: bool):
        """Enable or pause a source without deleting its ingested evidence."""
        row = self.repo.set_source_enabled(source_id, enabled)
        if row is None:
            raise IntelligenceServiceError("intelligence source not found")
        return self._source(row)

    def delete_source(self, source_id: int):
        """Remove a source configuration while keeping historical items readable."""
        if not self.repo.delete_source(source_id):
            raise IntelligenceServiceError("intelligence source not found")
        return {"ok": True, "source_id": source_id}

    def fetch_source(self, source_id: int, *, dry_run=False):
        """抓取单个情报源并按配置入库。

        Args:
            source_id: 要抓取的数据源 ID。
            dry_run: 为 True 时只解析不写入仓储、不更新状态，便于诊断与预览。

        Returns:
            含 ``fetched_count`` / ``saved_count`` / ``sample_items`` 等字段的结果字典。

        Raises:
            IntelligenceServiceError: 源不存在、源被禁用、上游抓取/解析失败时抛出；
                同时会把失败状态写回 ``last_status`` / ``last_error``。
        """
        source = self.repo.get_source(source_id)
        if not source: raise IntelligenceServiceError("intelligence source not found")
        if not source.enabled: raise IntelligenceServiceError("intelligence source is disabled")
        now = datetime.now()
        try:
            entries = self._fetch(self._source(source))[:self.config.news_intel_max_items_per_source]
            fields = [{"source_id": source.id, "source_name": source.name, "source_type": source.source_type,
                       "title": entry["title"], "summary": entry["summary"], "url": entry["url"], "source": source.name,
                       "published_at": entry["published_at"], "fetched_at": now, "scope_type": source.scope_type,
                       "scope_value": source.scope_value, "market": "cn", "raw_payload": json.dumps(entry, ensure_ascii=False)} for entry in entries]
            saved = 0 if dry_run else self.repo.upsert_items(fields)
            if not dry_run:
                # 非 dry-run 路径：执行保留期清理并把成功状态写回源
                self.repo.apply_retention(self.config.news_intel_retention_days); self.repo.update_source_status(source.id, status="success", fetched_at=now)
            return {"ok": True, "source_id": source.id, "fetched_count": len(entries), "saved_count": saved, "dry_run": dry_run, "sample_items": entries[:5]}
        except Exception as exc:
            # 失败时把异常信息裁剪到 500 字符后写回，便于运维排查而不暴露完整堆栈
            if not dry_run: self.repo.update_source_status(source.id, status="failed", error=str(exc)[:500])
            raise

    def fetch_enabled_sources(self):
        """批量抓取所有启用的情报源；单源失败不影响整体，返回逐源结果数组。"""
        rows, _ = self.repo.list_sources(enabled=True, market="cn")
        results = []
        for source in rows:
            try: results.append(self.fetch_source(source.id))
            # 单源失败时收敛为结构化错误条目加入结果，保证调用方可以拿到所有源的状态
            except Exception as exc: results.append({"ok": False, "source_id": source.id, "error": str(exc)[:300]})
        return {"ok": True, "source_count": len(rows), "saved_count": sum(item.get("saved_count", 0) for item in results), "results": results}

    def refresh_auto_sources(self, *, force=False):
        """每小时最多自动刷新一次已订阅的 A 股情报源，避免重复工作。"""
        if not self.config.news_intel_auto_fetch_enabled:
            return {"ok": True, "skipped": True, "reason": "disabled"}

        cls = type(self)
        with cls._auto_fetch_condition:
            waited_for_refresh = False
            while cls._auto_fetch_in_progress:
                waited_for_refresh = True
                cls._auto_fetch_condition.wait()
            if waited_for_refresh and cls._auto_fetch_last_result is not None:
                return dict(cls._auto_fetch_last_result)
            if (
                not force
                and cls._auto_fetch_last_run_at is not None
                and (datetime.now() - cls._auto_fetch_last_run_at).total_seconds()
                < _AUTO_FETCH_MIN_INTERVAL_SECONDS
            ):
                return {"ok": True, "skipped": True, "reason": "cooldown"}
            cls._auto_fetch_in_progress = True
        try:
            self.create_default_sources(enabled=True)
            fetch = self.fetch_enabled_sources()
            result = {"ok": True, "skipped": False, "fetch": fetch, "saved_count": fetch["saved_count"]}
        except Exception as exc:
            result = {"ok": False, "skipped": False, "error": str(exc)[:300]}
        finally:
            with cls._auto_fetch_condition:
                cls._auto_fetch_last_run_at = datetime.now()
                cls._auto_fetch_last_result = dict(result)
                cls._auto_fetch_in_progress = False
                cls._auto_fetch_condition.notify_all()
        return result

    def list_items(self, **filters):
        """分页查询情报条目，支持按市场范围、关键字、最近天数过滤。"""
        rows, total = self.repo.list_items(market="cn", scope_type=filters.get("scope_type"), scope_value=filters.get("scope_value"), query=filters.get("query"), days=filters.get("days"), page=filters.get("page", 1), page_size=filters.get("page_size", 50))
        return {"items": [self._item(row) for row in rows], "total": total, "page": filters.get("page", 1), "page_size": filters.get("page_size", 50)}

    def get_market_evidence(self, *, limit=8, days=3):
        """提取最近 ``days`` 天的市场级情报条目（默认 8 条 / 3 天），用于报告上下文注入。"""
        return self.list_items(scope_type="market", days=days, page_size=limit)["items"]

    def _fields(self, payload):
        """把外部请求字段归一化为仓储所需的字段结构，并做基础合法性校验。"""
        source_type = str(payload.get("source_type", "rss")).lower().strip()
        market = str(payload.get("market", "cn")).lower().strip()
        # 暂只允许 A 股的 rss/atom/newsnow 源类型，避免其它市场混入污染数据
        if source_type not in _ALLOWED_TYPES or market != "cn": raise IntelligenceServiceError("only A-share rss, atom, and NewsNow sources are supported")
        name, url = str(payload.get("name", "")).strip(), str(payload.get("url", "")).strip()
        if not name or not url: raise IntelligenceServiceError("source name and url are required")
        return {"name": name[:100], "url": url, "source_type": source_type, "enabled": bool(payload.get("enabled", True)), "scope_type": str(payload.get("scope_type", "market")), "scope_value": payload.get("scope_value") or None, "market": "cn", "description": str(payload.get("description") or "")[:1000] or None}

    @staticmethod
    def _blocked(host):
        """判断 host 是否指向内网/本机；DNS 解析失败时按字面 host 进行启发式判断。"""
        try: return not ipaddress.ip_address(host).is_global
        except ValueError: return host in {"localhost", "localhost.localdomain"} or host.endswith(".local")

    def _validate_url(self, url):
        """URL 入口校验：协议必须为 http(s)、禁止携带凭据、禁止指向内网/本机。"""
        parsed = urlparse(url); host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password: raise IntelligenceServiceError("source url must be a credential-free absolute http(s) URL")
        if self._blocked(host): raise IntelligenceServiceError("source url must not target private or local network")
        try: addresses = socket.getaddrinfo(host, None)
        except OSError as exc: raise IntelligenceServiceError("source url DNS lookup failed") from exc
        # 同时校验所有解析出来的 IP，防止 DNS rebinding 绕过 host 检查
        if not addresses or any(self._blocked(info[4][0]) for info in addresses): raise IntelligenceServiceError("source url must not target private or local network")

    def _fetch(self, source):
        """抓取 A 股情报源，并对每次跳转都重新做一次 DNS 校验。"""
        self._validate_url(source["url"])
        timeout = max(1, min(float(self.config.news_intel_fetch_timeout_sec), 30))
        headers = {"User-Agent": "daily-stock-analysis-intel/1.0"}
        response = None
        request_url = source["url"]
        try:
            if source["source_type"] == "newsnow":
                response = self._get_with_validated_dns(
                    request_url, timeout=timeout, headers=headers, allow_redirects=False, stream=True
                )
                if int(getattr(response, "status_code", 200)) in _REDIRECT_STATUS_CODES:
                    raise IntelligenceServiceError("NewsNow API redirects are not followed")
                response.raise_for_status()
            else:
                for _ in range(_MAX_REDIRECTS + 1):
                    response = self._get_with_validated_dns(
                        request_url, timeout=timeout, headers=headers, allow_redirects=False, stream=True
                    )
                    if int(getattr(response, "status_code", 200)) not in _REDIRECT_STATUS_CODES:
                        response.raise_for_status()
                        break
                    location = getattr(response, "headers", {}).get("Location")
                    response.close()
                    response = None
                    if not location:
                        raise IntelligenceServiceError("feed redirect missing Location header")
                    request_url = urljoin(request_url, location)
                    self._validate_url(request_url)
                else:
                    raise IntelligenceServiceError(f"feed redirect chain exceeds {_MAX_REDIRECTS}")
            self._validate_url(getattr(response, "url", None) or request_url)
            content = self._read_limited_response(response)
        except IntelligenceServiceError:
            raise
        except requests.RequestException as exc:
            raise IntelligenceServiceError("upstream request failed") from exc
        finally:
            if response is not None:
                response.close()
        return self._newsnow(content) if source["source_type"] == "newsnow" else self._feed(content)

    @staticmethod
    def _read_limited_response(response):
        """流式读取响应体并做字节上限保护，超限直接抛错防止内存被打满。"""
        if hasattr(response, "iter_content") and callable(response.iter_content):
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > _MAX_BYTES:
                    raise IntelligenceServiceError("feed response is too large")
                chunks.append(chunk)
            return b"".join(chunks)
        content = response.content[: _MAX_BYTES + 1]
        if len(content) > _MAX_BYTES:
            raise IntelligenceServiceError("feed response is too large")
        return content

    def _get_with_validated_dns(self, raw_url, **kwargs):
        """防止校验通过后 DNS 重新解析到内网地址，绕过 host 名校验。"""
        target_hostname = self._normalize_hostname(urlparse(raw_url).hostname)
        original_getaddrinfo = socket.getaddrinfo

        def guarded_getaddrinfo(host, port, *args, **inner_kwargs):
            """DNS 守卫：目标主机解析到内网/私网地址时抛错拦截。"""
            addresses = original_getaddrinfo(host, port, *args, **inner_kwargs)
            if self._normalize_hostname(host) == target_hostname:
                if not addresses or any(self._blocked(info[4][0]) for info in addresses):
                    raise IntelligenceServiceError("source url must not target private or local network")
            return addresses

        with _DNS_GUARD_LOCK:
            socket.getaddrinfo = guarded_getaddrinfo
            try:
                kwargs.setdefault("proxies", {"http": None, "https": None})
                return requests.get(raw_url, **kwargs)
            finally:
                socket.getaddrinfo = original_getaddrinfo

    @staticmethod
    def _normalize_hostname(host):
        """把主机名归一化为小写 IDNA 形式（bytes 先解码、去掉末尾点）。"""
        if isinstance(host, bytes):
            host = host.decode("ascii", errors="ignore")
        normalized = str(host or "").strip().lower().rstrip(".")
        try:
            return normalized.encode("idna").decode("ascii")
        except UnicodeError:
            return normalized

    def _newsnow(self, content):
        """解析 NewsNow JSON 响应，抽取每条条目的标题、摘要、URL、发布时间。"""
        try: items = json.loads(content.decode("utf-8")).get("items", [])
        except Exception as exc: raise IntelligenceServiceError("invalid NewsNow JSON") from exc
        return [entry for entry in (self._entry(item.get("title"), (item.get("extra") or {}).get("info") or "", item.get("url") or item.get("mobileUrl"), item.get("pubDate") or (item.get("extra") or {}).get("date")) for item in items if isinstance(item, dict)) if entry]

    def _feed(self, content):
        """解析 RSS/Atom XML 响应，抽取 ``item``/``entry`` 中的标题、摘要、链接、发布时间。"""
        try: root = ET.fromstring(content)
        except ET.ParseError as exc: raise IntelligenceServiceError("invalid RSS/Atom feed") from exc
        tag = root.tag.rsplit("}", 1)[-1].lower(); nodes = root.findall("./channel/item") if tag == "rss" else root.findall("./{*}entry")
        if tag not in {"rss", "feed"}: raise IntelligenceServiceError("unsupported feed format")
        return [entry for entry in (self._entry(self._text(node, "title"), self._text(node, "description") or self._text(node, "summary"), self._text(node, "link") or next((link.attrib.get("href") for link in node.findall("./{*}link") if link.attrib.get("href")), ""), self._text(node, "pubDate") or self._text(node, "published") or self._text(node, "updated")) for node in nodes) if entry]

    def _entry(self, title, summary, url, published):
        """归一化单条情报条目；缺 URL 时用哈希合成稳定占位 URL 以保证唯一性。"""
        title, summary, url = self._clean(title)[:300], self._clean(summary)[:2000], str(url or "").strip()
        if not title: return None
        if url: self._validate_url(url)
        # 没有原始链接时，用 title+published 哈希出稳定占位 URL，避免下游唯一索引冲突
        else: url = "no-url:intel:" + hashlib.sha256(f"{title}|{published}".encode()).hexdigest()[:24]
        return {"title": title, "summary": summary, "url": url, "published_at": self._date(published)}

    @staticmethod
    def _text(node, name):
        """从 XML 节点中读取子元素文本，兼容带命名空间与不带命名空间两种写法。"""
        value = node.find(f"./{{*}}{name}")
        if value is None:
            value = node.find(f"./{name}")
        return value.text.strip() if value is not None and value.text else ""
    @staticmethod
    def _clean(value):
        """去掉 HTML 标签并把空白压缩为单个空格后返回。"""
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value or ""))).strip()
    @staticmethod
    def _date(value):
        """把 RSS/Atom 各种日期字符串解析为 naive datetime；解析失败返回 ``None``。"""
        try:
            parsed = parsedate_to_datetime(str(value))
            # 统一为 naive UTC，便于存储与下游时区无关比较
            return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
        except Exception:
            try: return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError: return None
    @staticmethod
    def _source(row):
        """把数据源 ORM 行序列化为字典。"""
        return {key: getattr(row, key) for key in ("id", "name", "source_type", "url", "enabled", "scope_type", "scope_value", "market", "description", "last_status", "last_error")}
    @staticmethod
    def _item(row):
        """把情报条目 ORM 行序列化为字典（时间字段转 ISO 字符串）。"""
        return {key: (getattr(row, key).isoformat() if key in {"published_at", "fetched_at"} and getattr(row, key) else getattr(row, key)) for key in ("id", "source_id", "source_name", "source_type", "title", "summary", "url", "source", "published_at", "fetched_at", "scope_type", "scope_value", "market")}
