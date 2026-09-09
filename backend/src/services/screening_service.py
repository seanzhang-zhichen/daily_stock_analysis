# -*- coding: utf-8 -*-
"""DSA 原生股票选股服务。

本模块是 A 股 / 港股 / 美股智能分析系统的选股服务层，负责把内置的选股引擎
（`src.services.screening.pipeline`）以及东方财富、同花顺等外部热点数据源
封装为对外（API、调度器等）可调用的统一接口。

主要职责：
- 暴露选股服务的策略列表、运行历史、热点题材列表与题材详情等 API；
- 把 DSA 自有的实时行情、日线、新闻、事件、基本面能力注入到选股流水线，
  同时屏蔽跨进程的 LiteLLM 路由与 header 注入；
- 管理热点缓存、详情缓存、快照源优先级、运行历史持久化等"非业务但必需"的能力。

代码来源说明：本文件包含的部分实现源自 AlphaSift 项目，详细归属与许可证见
``THIRD_PARTY_NOTICES.md`` 以及相关文件头部的归属说明。
"""

from __future__ import annotations

import importlib
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import HTTPException
from pydantic import BaseModel, Field

from src.config import Config, get_configured_llm_models, normalize_llm_channel_api_surface
from src.services.screening import REFERENCE_PROJECT, REFERENCE_REVISION, __version__ as SCREENING_VERSION
from src.services.screening import hotspot as screening_hotspot
from src.services.screening.config import Config as ScreeningPipelineConfig
from src.services.screening.pipeline import screen as run_screening_pipeline
from src.services.screening.source_guard import parse_source_timeout_seconds
from src.services.screening.strategy import list_strategies as load_screening_strategies
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 模块级常量：选股服务在 DSA 体系内的固定参数 / 缓存 / 超时配置
# ---------------------------------------------------------------------------

# 由本服务代为管理的 LiteLLM provider 集合（仅这些走 DSA 注入逻辑）
SCREENING_MANAGED_LITELLM_PROVIDERS = frozenset({"gemini", "vertex_ai", "anthropic", "openai", "deepseek"})
# 选股服务对外契约版本号，配合 client/前端做兼容性判断
SCREENING_CONTRACT_VERSION = "1"
# 进程全局可重入锁：保护"运行时环境变量桥接"过程不被并发改写
_SCREENING_RUNTIME_ENV_LOCK = threading.RLock()
# 单次选股运行中最多走 DSA 增广的候选数量
DSA_ENRICHMENT_MAX_CANDIDATES = 3
# 预排序阶段轻量上下文最多携带的候选数量
DSA_PRE_RANK_CONTEXT_MAX_CANDIDATES = 3
# LLM 候选数相对用户请求量的放大倍数（用于给 LLM 留出筛选空间）
DSA_SCREENING_LLM_CANDIDATE_MULTIPLIER = 2
# LLM 候选数的硬上限，避免长列表打爆上下文
DSA_SCREENING_LLM_MAX_CANDIDATES = 12
# 日线抓取失败时的默认重试次数
DSA_SCREENING_DAILY_FETCH_RETRIES = 3
# 未配置 Tushare token 时的快照数据源优先级
DSA_SCREENING_SNAPSHOT_SOURCE_PRIORITY = "sina,efinance,akshare_em,em_datacenter"
# 配置了 Tushare token 时的快照数据源优先级（Tushare 优先）
DSA_SCREENING_SNAPSHOT_SOURCE_PRIORITY_WITH_TUSHARE = "tushare,sina,efinance,akshare_em,em_datacenter"
# 选股阶段候选上下文允许启用的数据源（按业务场景裁剪）
DSA_SCREENING_CANDIDATE_CONTEXT_PROVIDERS = "news,fund_flow,announcement,quote"
# 选股服务在磁盘上保存热点缓存、日线缓存等的根目录
DSA_SCREENING_DATA_DIR = Path("data") / "screening"
# 热点列表的本地缓存文件路径
DSA_SCREENING_HOTSPOT_CACHE_PATH = DSA_SCREENING_DATA_DIR / "hotspots.json"
# 热点历史（追加式 JSONL）的本地路径
DSA_SCREENING_HOTSPOT_HISTORY_PATH = DSA_SCREENING_DATA_DIR / "hotspot.history.jsonl"
# 热点列表至少需要的有效条目数，过少则视为不可信缓存
DSA_SCREENING_MIN_HOTSPOT_CACHE_COUNT = 3
# 热点列表缓存的默认有效期（秒）
DSA_SCREENING_HOTSPOT_CACHE_TTL_SECONDS = 10 * 60
# 热点详情缓存的默认有效期（秒）
DSA_SCREENING_HOTSPOT_DETAIL_CACHE_TTL_SECONDS = 30 * 60
# 热点事件摘要最多字符数（用于前端展示裁剪）
DSA_SCREENING_HOTSPOT_EVENT_SUMMARY_MAX_CHARS = 90
# 单次返回前端前主动预取的热点详情数量
DSA_SCREENING_HOTSPOT_PREFETCH_DETAIL_COUNT = 8
# 单次热点 provider 调用的超时上限（秒）
DSA_SCREENING_HOTSPOT_CALL_TIMEOUT_SECONDS = 8
# 热点消息搜索调用的超时上限（秒）
DSA_SCREENING_HOTSPOT_SEARCH_TIMEOUT_SECONDS = 12
# 东方财富热点源不可用时的错误码（前端据此降级提示）
DSA_SCREENING_HOTSPOT_UNAVAILABLE_CODE = "eastmoney_hotspot_unavailable"
# 东方财富热点源不可用时的前端提示文案
DSA_SCREENING_HOTSPOT_UNAVAILABLE_MESSAGE = "热点源连接中断，暂无可用缓存。"
# 识别"东财网络层瞬时故障"的关键字串，用于决定是否回退到缓存/降级提示
DSA_SCREENING_HOTSPOT_CONNECTIVITY_ERROR_MARKERS = (
    "remote disconnected",
    "remote end closed connection",
    "connection aborted",
    "connection reset",
    "connection refused",
    "connection timed out",
    "read timed out",
    "connecttimeout",
    "readtimeout",
    "max retries exceeded",
    "chunkedencodingerror",
    "protocolerror",
    "incompleteread",
)
# 延迟初始化 DSA 数据获取管理器时使用的锁（避免重复构造）
_DSA_FETCHER_MANAGER_LOCK = threading.RLock()
_DSA_FETCHER_MANAGER: Any = None
# 选股/基本面分析需要关注的基础数据维度（按需裁剪上下文）
_FUNDAMENTAL_BLOCKS = ("valuation", "growth", "earnings", "institution", "capital_flow", "boards")
# 选股调用 litellm completion 时注入的 header 路由表（ContextVar 跨调用栈传递）
_SCREENING_LITELLM_COMPLETION_ROUTES: ContextVar[Optional[Tuple[Dict[str, Any], ...]]] = ContextVar(
    "screening_litellm_completion_routes",
    default=None,
)
# 热点 provider 调用的硬截止时间（deadline），用于可中断子进程/连接
_DSA_HOTSPOT_CALL_DEADLINE: ContextVar[Optional[float]] = ContextVar(
    "dsa_hotspot_call_deadline",
    default=None,
)
# 标记已注入 DSA header 桥接的 litellm completion 函数属性名
_SCREENING_LITELLM_COMPLETION_ATTR = "_screening_litellm_completion_bridge"
# 桥接函数安装时的进程级互斥锁
_SCREENING_LITELLM_COMPLETION_LOCK = threading.Lock()


def _safe_float(value: Any) -> Optional[float]:
    """把任意输入安全地转成 ``float``，失败或非有限值时返回 ``None``。

    用于把候选股行、行情字段等可能含字符串/``None``/``NaN`` 的数据规整成数值。
    """
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO8601 字符串（秒精度、``Z`` 后缀）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _resolve_screening_data_dir() -> Path:
    """根据环境变量解析选股缓存根目录，未配置则使用默认值。"""
    configured = _env_text(os.getenv("SCREENING_DATA_DIR"))
    if configured:
        return Path(configured)
    return DSA_SCREENING_DATA_DIR


def _screening_hotspot_cache_path() -> Path:
    """返回当前生效的热点列表缓存文件路径。"""
    if _env_text(os.getenv("SCREENING_DATA_DIR")):
        return _resolve_screening_data_dir() / "hotspots.json"
    return DSA_SCREENING_HOTSPOT_CACHE_PATH


def _screening_hotspot_history_path() -> Path:
    """返回当前生效的热点历史 JSONL 文件路径。"""
    if _env_text(os.getenv("SCREENING_DATA_DIR")):
        return _resolve_screening_data_dir() / "hotspot.history.jsonl"
    return DSA_SCREENING_HOTSPOT_HISTORY_PATH


def _screening_hotspot_detail_cache_dir() -> Path:
    """返回热点详情缓存所在的目录路径。"""
    return _resolve_screening_data_dir() / "hotspot_details"


def _screening_hotspot_detail_cache_path(*, provider: str, topic: str) -> Path:
    """生成单个 (provider, topic) 对应的热点详情缓存文件路径。

    通过对 provider+topic 拼接做 SHA1，把不同题材映射到稳定的文件名。
    """
    provider_text = re.sub(r"[^A-Za-z0-9_.-]+", "_", _env_text(provider) or "akshare")
    digest = hashlib.sha1(f"{provider_text}\0{_env_text(topic)}".encode("utf-8")).hexdigest()
    return _screening_hotspot_detail_cache_dir() / f"{provider_text}.{digest}.json"


def _parse_cache_datetime(value: Any) -> Optional[datetime]:
    """解析缓存字段里的 ISO8601 时间字符串，失败时返回 ``None``。"""
    text = _env_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _strip_hotspot_search_augmentation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """返回剥离了"本次请求专用的搜索数据"后的可缓存热点详情。

    单次请求在线索页上调用搜索服务时，会在 ``route`` / ``timeline`` 里追加
    ``search_result=True`` 的临时项，这里把它们剔除，保证写入磁盘的缓存不带
    用户级会话状态。
    """
    base = dict(payload)
    for key in ("route", "timeline"):
        rows = base.get(key)
        if isinstance(rows, list):
            base[key] = [
                item
                for item in rows
                if not (isinstance(item, dict) and bool(item.get("search_result")))
            ]
    base.pop("news_search_requested", None)
    base.pop("news_search_status", None)
    return base


def _load_screening_hotspot_detail_cache(
    *,
    provider: str,
    topic: str,
    allow_stale: bool = False,
) -> Optional[Dict[str, Any]]:
    """读取单个题材的热点详情缓存。

    Args:
        provider: 数据源名（用于文件命名隔离）。
        topic: 题材名。
        allow_stale: 是否允许返回过期或"降级 leader-only"数据。

    Returns:
        命中且未过期时返回详情字典，否则 ``None``。
    """
    cache_path = _screening_hotspot_detail_cache_path(provider=provider, topic=topic)
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Failed to read Screening hotspot detail cache from %s: %s", cache_path, exc)
        return None

    payload = raw.get("payload") if isinstance(raw, dict) else None
    if not isinstance(payload, dict):
        return None
    # 不把"只有领涨股的降级响应"当作完整的 detail 缓存：
    # 这种数据可作为显式的过期回退，但作为新鲜数据复用会让后续每次点击都"看起来只有一只成份股"。
    if not allow_stale and (
        bool(payload.get("fallback_used"))
        or bool(payload.get("stale"))
        or "live_stocks" in _list_text_values(payload.get("missing_fields"))
    ):
        return None
    cached_at = raw.get("cached_at") or payload.get("cached_at")
    cached_dt = _parse_cache_datetime(cached_at)
    if cached_dt is None:
        return None
    age_seconds = max(0.0, (datetime.now(timezone.utc) - cached_dt).total_seconds())
    stale = age_seconds > DSA_SCREENING_HOTSPOT_DETAIL_CACHE_TTL_SECONDS
    if stale and not allow_stale:
        return None

    cached = _ensure_hotspot_detail_compat_fields(
        _strip_hotspot_search_augmentation(payload)
    )
    cached.update({
        "enabled": True,
        "provider": provider or cached.get("provider") or "akshare",
        "cache_used": True,
        "cached_at": cached_at,
        "stale": bool(cached.get("stale") or stale),
    })
    if stale:
        cached["fallback_used"] = True
        cached["stale_age_seconds"] = round(age_seconds, 1)
    return _remove_non_finite_json_values(cached)


def _write_screening_hotspot_detail_cache(*, provider: str, topic: str, payload: Dict[str, Any]) -> None:
    """把单题材的热点详情写入本地 JSON 缓存文件。

    写入前会清理掉请求级搜索增强，并补齐 ``stocks`` / ``leader_stocks`` 兼容字段。
    """
    cache_path = _screening_hotspot_detail_cache_path(provider=provider, topic=topic)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cleaned = _remove_non_finite_json_values(
            _ensure_hotspot_detail_compat_fields(
                _strip_hotspot_search_augmentation(payload)
            )
        )
        cached_at = _utc_now_iso()
        cache_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": provider or cleaned.get("provider") or "akshare",
                    "topic": topic,
                    "cached_at": cached_at,
                    "payload": cleaned,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to write Screening hotspot detail cache for %s: %s", topic, exc)


def _ensure_hotspot_detail_compat_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """保证新旧两套热点详情消费者看到一致的字段形状。

    主要补齐 ``stocks`` 与 ``leader_stocks`` 互为镜像、``stock_count`` 与列表长度一致等
    兼容性要求。
    """
    stocks = payload.get("stocks")
    leader_stocks = payload.get("leader_stocks")
    if not isinstance(stocks, list):
        stocks = []
    if not isinstance(leader_stocks, list) or not leader_stocks:
        nested_leader_stocks = _extract_nested_hotspot_leader_stocks(payload)
        leader_stocks = nested_leader_stocks or (leader_stocks if isinstance(leader_stocks, list) else [])
    if not stocks and leader_stocks:
        stocks = leader_stocks
    if not leader_stocks and stocks:
        leader_stocks = stocks
    payload["stocks"] = stocks
    payload["leader_stocks"] = leader_stocks
    payload["stock_count"] = len(stocks)
    return payload


def _extract_nested_hotspot_leader_stocks(payload: Dict[str, Any]) -> List[Any]:
    """从嵌套的 ``summary`` / ``summary_detail`` 里兜底提取领涨股列表。"""
    for key in ("summary_detail", "summary"):
        summary = payload.get(key)
        if not isinstance(summary, dict):
            continue
        leader_stocks = summary.get("leader_stocks")
        if isinstance(leader_stocks, list) and leader_stocks:
            return leader_stocks
    return []


def _screening_hotspot_cache_ttl_seconds() -> Optional[float]:
    """返回热点列表缓存的有效期；``0`` 表示禁用新鲜缓存复用。"""
    raw = os.getenv("SCREENING_HOTSPOT_CACHE_TTL_SEC")
    if raw is None or not raw.strip():
        return float(DSA_SCREENING_HOTSPOT_CACHE_TTL_SECONDS)
    try:
        ttl = float(raw.strip())
    except (TypeError, ValueError):
        logger.warning(
            "SCREENING_HOTSPOT_CACHE_TTL_SEC=%r is not a valid number; falling back to %ss",
            raw,
            DSA_SCREENING_HOTSPOT_CACHE_TTL_SECONDS,
        )
        return float(DSA_SCREENING_HOTSPOT_CACHE_TTL_SECONDS)
    return ttl if ttl > 0 else None


def _load_screening_hotspot_cache(
    *,
    provider: str,
    top: int,
    allow_stale: bool = False,
) -> Optional[Dict[str, Any]]:
    """读取热点列表缓存，过期或条目过少时返回 ``None``。

    Args:
        provider: 期望的数据源名。
        top: 前端实际想要的数量，命中后会裁剪返回的 ``hotspots``。
        allow_stale: 是否允许返回过期的缓存（用作 live 失败的兜底）。

    Returns:
        命中时返回裁剪后的热点字典，否则 ``None``。
    """
    cache_path = _screening_hotspot_cache_path()
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Failed to read Screening hotspot cache from %s: %s", cache_path, exc)
        return None

    payload = _normalize_screening_hotspot_cache_payload(raw)
    if not isinstance(payload, dict):
        return None
    hotspots = payload.get("hotspots")
    if not isinstance(hotspots, list) or not hotspots:
        return None

    top_count = max(1, min(int(top or 12), 50))
    if len(hotspots) < min(DSA_SCREENING_MIN_HOTSPOT_CACHE_COUNT, top_count):
        logger.info(
            "Ignoring Screening hotspot cache with too few rows: %s < %s",
            len(hotspots),
            min(DSA_SCREENING_MIN_HOTSPOT_CACHE_COUNT, top_count),
        )
        return None

    cached_at = raw.get("cached_at") or payload.get("cached_at") or raw.get("generated_at")
    cached_dt = _parse_cache_datetime(cached_at)
    age_seconds: Optional[float] = None
    if cached_dt is not None:
        age_seconds = max(0.0, (datetime.now(timezone.utc) - cached_dt).total_seconds())
    ttl_seconds = _screening_hotspot_cache_ttl_seconds()
    stale = cached_dt is None or ttl_seconds is None or age_seconds > ttl_seconds
    if stale and not allow_stale:
        return None

    selected = hotspots[:top_count]
    cached = dict(payload)
    cached.update({
        "enabled": True,
        "provider": provider or payload.get("provider") or "akshare",
        "hotspots": selected,
        "hotspot_count": len(selected),
        "cache_used": True,
        "cached_at": cached_at,
        "stale": stale,
        "stale_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "stale_age_hours": round(age_seconds / 3600, 3) if age_seconds is not None else None,
    })
    if stale:
        cached["fallback_used"] = True
    cached["source_errors"] = list(cached.get("source_errors") or [])
    return _remove_non_finite_json_values(cached)


def _normalize_screening_hotspot_cache_payload(raw: Any) -> Optional[Dict[str, Any]]:
    """把磁盘上的旧/新两种缓存格式归一化为统一结构。"""
    if not isinstance(raw, dict):
        return None
    payload = raw.get("payload")
    if isinstance(payload, dict):
        return payload
    hotspots = raw.get("hotspots")
    if not isinstance(hotspots, list):
        return None
    metadata_raw = raw.get("metadata")
    metadata: Dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    cached_at = raw.get("cached_at") or raw.get("generated_at") or metadata.get("generated_at")
    return {
        "enabled": True,
        "provider": _env_text(metadata.get("provider")) or "akshare",
        "provider_used": _env_text(metadata.get("provider_used")),
        "fallback_used": False,
        "cache_used": False,
        "cached_at": cached_at,
        "schema_version": raw.get("schema_version") or metadata.get("schema_version"),
        "source_errors": _list_text_values(raw.get("source_errors") or metadata.get("source_errors")),
        "stale": bool(raw.get("stale") or metadata.get("stale") or False),
        "stale_age_hours": raw.get("stale_age_hours") or metadata.get("stale_age_hours"),
        "hotspots": hotspots,
        "hotspot_count": len(hotspots),
    }


@dataclass(frozen=True)
class _HotspotSearchAugmentation:
    """单题材"事件搜索增强"的结果封装。

    Attributes:
        routes: 命中并摘要后的搜索结果条目（最多 2 条）。
        status: 整体状态字符串（``available`` / ``no_results`` / ``unavailable``）。
    """

    routes: List[Dict[str, Any]]
    status: str


def _build_hotspot_event_routes_from_search(topic: str) -> _HotspotSearchAugmentation:
    """为指定题材调用 DSA 搜索服务，构造可拼接到 detail 的事件路由。"""
    topic_text = _env_text(topic)
    if not topic_text:
        return _HotspotSearchAugmentation(routes=[], status="unavailable")
    try:
        service = _get_dsa_search_service()
        if not getattr(service, "is_available", False):
            return _HotspotSearchAugmentation(routes=[], status="unavailable")
        configured_timeout = parse_source_timeout_seconds(
            "SCREENING_HOTSPOT_SEARCH_TIMEOUT_SEC",
            default=DSA_SCREENING_HOTSPOT_SEARCH_TIMEOUT_SECONDS,
        )
        response = service.search_topic_news_bounded(
            topic_text,
            max_results=3,
            focus_keywords=[f'"{topic_text}"', "A股", "最新消息", "催化"],
            # 受限搜索会启动可终止的子进程，因此必须设置一个硬性 deadline；
            # 即便上层筛股禁用调用方超时，也会落到这个安全上限。
            timeout_seconds=(
                configured_timeout
                if configured_timeout is not None
                else float(DSA_SCREENING_HOTSPOT_SEARCH_TIMEOUT_SECONDS)
            ),
        )
    except Exception as exc:
        logger.info("Screening hotspot event search skipped for %s: %s", topic_text, exc)
        return _HotspotSearchAugmentation(routes=[], status="unavailable")

    if not bool(getattr(response, "success", False)):
        return _HotspotSearchAugmentation(routes=[], status="unavailable")
    today = datetime.now().date().isoformat()
    routes: List[Dict[str, Any]] = []
    for result in list(getattr(response, "results", []) or []):
        title = _env_text(getattr(result, "title", ""))
        snippet = _env_text(getattr(result, "snippet", ""))
        if not title and not snippet:
            continue
        url = _normalize_external_http_url(getattr(result, "url", ""))
        if not url:
            continue
        published = _env_text(getattr(result, "published_date", ""))
        source = _env_text(getattr(result, "source", "")) or _env_text(getattr(response, "provider", "")) or "news_search"
        description = _summarize_hotspot_news_event(
            topic=topic_text,
            title=title,
            snippet=snippet,
        )
        if not description:
            continue
        date = _extract_date_text(published) or _extract_date_text(description) or today
        routes.append({
            "title": _truncate_text(title, 48) or "消息催化",
            "description": description,
            "source": source,
            "date": date,
            "published_at": published or date,
            "url": url,
            "search_result": True,
        })
        if len(routes) >= 2:
            break
    return _HotspotSearchAugmentation(
        routes=routes,
        status="available" if routes else "no_results",
    )


def _normalize_external_http_url(value: Any) -> str:
    """仅接受绝对 HTTP(S) 链接，供面向用户的搜索事件引用。"""
    text = _env_text(value)
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def _with_hotspot_search_augmentation(payload: Dict[str, Any], *, topic: str) -> Dict[str, Any]:
    """在不修改基础 detail 的前提下，给响应追加"按需启用"的搜索结果。"""
    augmented = _strip_hotspot_search_augmentation(payload)
    search_result = _build_hotspot_event_routes_from_search(topic)
    search_routes = search_result.routes
    if search_routes:
        route = augmented.get("route")
        existing_routes = route if isinstance(route, list) else []
        timeline = augmented.get("timeline")
        existing_timeline = timeline if isinstance(timeline, list) else []
        combined_routes = [*search_routes, *existing_routes]
        augmented["route"] = combined_routes
        # 搜索是"仅展示层"的附加项；原始 timeline 字段保持原样，不要被展示态的 route 覆盖。
        augmented["timeline"] = [*search_routes, *existing_timeline]
    augmented["news_search_requested"] = True
    augmented["news_search_status"] = search_result.status
    return _remove_non_finite_json_values(augmented)


def _summarize_hotspot_news_event(*, topic: str, title: str, snippet: str) -> str:
    """把一条搜索结果摘要成"用于展示的事件描述"。"""
    compact_text = _compact_hotspot_news_text(title=title, snippet=snippet)
    return _summarize_hotspot_news_event_locally(topic=topic, text=compact_text)


def _summarize_hotspot_news_event_locally(*, topic: str, text: str) -> str:
    """本地拼接"催化剂 + 影响"句式的事件摘要。"""
    cleaned = _strip_hotspot_news_noise(text)
    if not cleaned:
        return ""
    catalyst = _extract_hotspot_catalyst_phrase(cleaned)
    impacts = _extract_hotspot_impact_phrases(cleaned)
    if catalyst and impacts:
        summary = f"{catalyst}，带动{impacts}发酵。"
    elif catalyst:
        summary = f"{catalyst}，市场关注{topic}相关产业链机会。"
    else:
        summary = _first_meaningful_hotspot_sentence(cleaned)
    summary = _truncate_text(summary, DSA_SCREENING_HOTSPOT_EVENT_SUMMARY_MAX_CHARS).rstrip(".。…")
    return _truncate_text(f"{summary}。", DSA_SCREENING_HOTSPOT_EVENT_SUMMARY_MAX_CHARS)


def _strip_hotspot_news_noise(text: str) -> str:
    """清洗原始新闻文本，去掉【方括号】、日期、交易数据等噪音片段。"""
    cleaned = _normalize_inline_text(text)
    cleaned = re.sub(r"【[^】]{1,24}】", " ", cleaned)
    cleaned = re.sub(r"\[[^\]]{1,24}\]", " ", cleaned)
    cleaned = re.sub(r"\b20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}[日号]?\b", " ", cleaned)
    cleaned = re.sub(r"\b\d{1,2}:\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"\([^)]{0,18}\d+\.\d+[^)]{0,18}\)", " ", cleaned)
    cleaned = re.sub(r"（[^）]{0,18}\d+\.\d+[^）]{0,18}）", " ", cleaned)
    cleaned = re.sub(r"截至[^。；;]*", " ", cleaned)
    cleaned = re.sub(r"(建议关注|后续建议|风险提示|投资建议)[^。；;]*", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" ，,；;。.")


def _extract_hotspot_catalyst_phrase(text: str) -> str:
    """从清洗后的文本中抽取一句"催化剂"短语。"""
    patterns = (
        r"以[^，。；;]{1,12}代[^，。；;]{1,12}",
        r"[^，。；;]{1,18}(涨价|价格上行|供需偏紧|供应紧张|资源增储|订单增长|政策催化|出口管制|减产|并购重组|技术突破)[^，。；;]{0,24}",
        r"[^，。；;]{1,18}(替代|国产替代|需求增长|景气上行)[^，。；;]{0,24}",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _normalize_inline_text(match.group(0)).strip(" ，,；;。.")
    return ""


def _extract_hotspot_impact_phrases(text: str) -> str:
    """从文本中识别受影响的产业链标签并以顿号拼接。"""
    impacts: List[str] = []
    keyword_groups = (
        ("小金属", ("小金属", "钼", "钨", "锑", "锗", "铟")),
        ("有色金属", ("有色", "铜", "铝", "锌", "铅")),
        ("相关个股", ("涨停", "异动", "走强", "大涨", "拉升")),
        ("产业链", ("产业链", "上游", "下游", "材料", "资源")),
    )
    for label, keywords in keyword_groups:
        if any(keyword in text for keyword in keywords) and label not in impacts:
            impacts.append(label)
    return "、".join(impacts[:3])


def _first_meaningful_hotspot_sentence(text: str) -> str:
    """挑出第一条长度足够且不包含交易数据的句子作为摘要。"""
    sentences = [
        _normalize_inline_text(item).strip(" ，,；;。.")
        for item in re.split(r"[。！？!?；;]", text)
        if _normalize_inline_text(item)
    ]
    for sentence in sentences:
        if len(sentence) >= 8 and not re.search(r"(现价|成交额|涨跌幅|换手率|建议关注|截至)", sentence):
            return sentence
    return sentences[0] if sentences else text


def _compact_hotspot_news_text(*, title: str, snippet: str) -> str:
    """把"标题 + 摘要"压成一段适合摘要生成的紧凑文本。"""
    title_text = _normalize_inline_text(title)
    snippet_text = _normalize_inline_text(snippet)
    if title_text and snippet_text.startswith(title_text):
        snippet_text = snippet_text[len(title_text):].lstrip(" ：:，,。;；")
    if title_text and snippet_text == title_text:
        snippet_text = ""
    text = "。".join(part for part in (title_text, snippet_text) if part)
    text = re.sub(r"(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}[日号]?)\s+\d{1,2}:\d{2}", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_inline_text(value: Any) -> str:
    """把任意输入归一化成一个不含换行/Tab、首尾无空白的纯文本。"""
    text = _env_text(value)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _truncate_text(text: str, max_chars: int) -> str:
    """在句末标点处优先截断，避免截到半个词；超长时回退硬截并补省略号。"""
    text = _normalize_inline_text(text)
    if len(text) <= max_chars:
        return text
    sentence_parts = re.split(r"(?<=[。！？!?；;])", text)
    summary = ""
    for part in sentence_parts:
        if not part:
            continue
        if len(summary) + len(part) > max_chars:
            break
        summary += part
    if summary:
        return summary.rstrip("，,；;：: ")[:max_chars].rstrip("，,；;：: ") + "..."
    return text[: max(0, max_chars - 3)].rstrip("，,；;：: ") + "..."


def _extract_date_text(text: str) -> str:
    """从文本里抽出形如 ``YYYY-MM-DD`` 的日期字符串；找不到则返回空串。"""
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text or "")
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _hotspot_rows_are_thin(rows: List[Any], *, top: int) -> bool:
    """判断热点数据是否"信息稀薄"（缺少涨跌/趋势指标），用于触发直接拉取。"""
    if len(rows) < min(DSA_SCREENING_MIN_HOTSPOT_CACHE_COUNT, max(1, top)):
        return True
    rich_count = 0
    metric_count = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        if item.get("change_pct") is not None or item.get("changePct") is not None:
            rich_count += 1
        if (
            item.get("trend_score") is not None
            or item.get("trendScore") is not None
            or item.get("persistence_score") is not None
            or item.get("persistenceScore") is not None
        ):
            metric_count += 1
    return rich_count == 0 or metric_count == 0


def _snake_to_camel(value: str) -> str:
    """把 ``snake_case`` 字符串转成 ``camelCase``（仅首段不变，后续首字母大写）。"""
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _enrich_hotspot_rows_from_provider(rows: List[Any], provider: Any, *, top: int) -> List[Dict[str, Any]]:
    """用 provider 直拉的指标回填已有热点行缺失的字段。"""
    try:
        provider_rows = provider.hotspot_rows(top=max(top, len(rows), 30))
    except Exception as exc:
        logger.warning("Screening hotspot metric enrichment failed: %s", exc)
        return [dict(item) if isinstance(item, dict) else item for item in rows]
    by_topic: Dict[str, Dict[str, Any]] = {}
    for item in provider_rows or []:
        if not isinstance(item, dict):
            continue
        topic = _env_text(item.get("topic") or item.get("name"))
        if topic:
            by_topic[topic] = item
        name = _env_text(item.get("name"))
        if name and "·" in name:
            by_topic[name.split("·")[-1].strip()] = item
    enriched: List[Dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            enriched.append(raw)
            continue
        item = dict(raw)
        topic = _env_text(item.get("topic") or item.get("name"))
        provider_item = by_topic.get(topic)
        if not provider_item:
            enriched.append(item)
            continue
        for key in (
            "change_pct",
            "heat_score",
            "trend_score",
            "persistence_score",
            "observations",
            "stage",
            "state",
            "sample_stock_count",
            "leaders",
            "theme_group",
        ):
            camel_key = _snake_to_camel(key)
            if item.get(key) in (None, "", [], {}) and item.get(camel_key) in (None, "", [], {}):
                value = provider_item.get(key)
                if value not in (None, "", [], {}):
                    item[key] = value
        if item.get("name") in (None, "", topic):
            item["name"] = provider_item.get("name") or topic
        enriched.append(item)
    return enriched


def _write_screening_hotspot_cache(payload: Dict[str, Any]) -> None:
    """把热点列表写入本地缓存；同源时会与已有缓存合并，避免覆盖更新的行。"""
    cache_path = _screening_hotspot_cache_path()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cached_at = _utc_now_iso()
        cache_payload = dict(payload)
        cache_payload["cache_used"] = False
        cache_payload["cached_at"] = cached_at
        current_hotspots = cache_payload.get("hotspots")
        if not isinstance(current_hotspots, list):
            current_hotspots = []
            cache_payload["hotspots"] = current_hotspots
        existing_payload = _load_screening_hotspot_cache_payload_for_write(cache_path)
        if isinstance(existing_payload, dict):
            existing_provider = _env_text(existing_payload.get("provider"))
            current_provider = _env_text(cache_payload.get("provider"))
            existing_hotspots = existing_payload.get("hotspots")
            if (
                isinstance(existing_hotspots, list)
                and existing_hotspots
                and (not current_provider or not existing_provider or existing_provider == current_provider)
            ):
                merged_hotspots = _merge_screening_hotspot_cache_rows(current_hotspots, existing_hotspots)
                if len(merged_hotspots) > len(current_hotspots):
                    cache_payload["hotspots"] = merged_hotspots
                    cache_payload["hotspot_count"] = len(merged_hotspots)
                    cache_payload["details"] = _merge_screening_hotspot_cache_details(
                        cache_payload.get("details"),
                        existing_payload.get("details"),
                    )
        cache_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "generated_at": cached_at,
                    "cached_at": cached_at,
                    "metadata": {
                        "schema_version": 2,
                        "asset_type": "hotspot_cache",
                        "provider": cache_payload.get("provider"),
                        "provider_used": cache_payload.get("provider_used"),
                        "row_count": len(cache_payload.get("hotspots") or []),
                        "source_errors": _list_text_values(cache_payload.get("source_errors")),
                    },
                    "hotspots": cache_payload.get("hotspots") or [],
                    "payload": cache_payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to write Screening hotspot cache to %s: %s", cache_path, exc)


def _merge_screening_hotspot_cache_rows(current_rows: List[Any], existing_rows: List[Any]) -> List[Any]:
    """把两份热点行按题材去重拼接，保留较长的那一侧的总条数。"""
    merged: List[Any] = []
    seen_topics: set[str] = set()
    target_count = max(len(current_rows), len(existing_rows))

    def append_rows(rows: List[Any]) -> None:
        """把一批行追加进 merged：dict 按题材去重，其它按相等去重。"""
        for row in rows:
            if isinstance(row, dict):
                topic = _hotspot_topic_from_row(row)
                if topic:
                    if topic in seen_topics:
                        continue
                    seen_topics.add(topic)
                merged.append(dict(row))
                continue
            if row in merged:
                continue
            merged.append(row)

    append_rows(current_rows)
    append_rows(existing_rows)
    return merged[:target_count]


def _merge_screening_hotspot_cache_details(current_details: Any, existing_details: Any) -> Dict[str, Any]:
    """合并两份 ``details`` 字典（新值优先）。"""
    merged: Dict[str, Any] = {}
    if isinstance(existing_details, dict):
        merged.update(existing_details)
    if isinstance(current_details, dict):
        merged.update(current_details)
    return merged


def _load_screening_hotspot_cache_payload_for_write(cache_path: Path) -> Optional[Dict[str, Any]]:
    """读取已有缓存并归一化为内部结构，仅供写入时合并使用。"""
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Failed to read existing Screening hotspot cache from %s: %s", cache_path, exc)
        return None
    return _normalize_screening_hotspot_cache_payload(raw)


def _hotspot_topic_from_row(row: Any) -> str:
    """从一条热点行里抽出题材名（兼容多种字段名）。"""
    if not isinstance(row, dict):
        return ""
    return _env_text(row.get("topic") or row.get("name") or row.get("canonical_topic"))


def _attach_cached_hotspot_details(
    payload: Dict[str, Any],
    *,
    provider: str,
    top: int,
) -> Dict[str, Any]:
    """把已有的题材详情缓存按题材挂到列表响应上，避免前端再发起一次详情请求。"""
    rows = payload.get("hotspots")
    if not isinstance(rows, list) or not rows:
        return payload
    details = dict(payload.get("details") if isinstance(payload.get("details"), dict) else {})
    for row in rows[:max(0, min(int(top or 0), DSA_SCREENING_HOTSPOT_PREFETCH_DETAIL_COUNT))]:
        topic = _hotspot_topic_from_row(row)
        if not topic or topic in details:
            continue
        cached = _load_screening_hotspot_detail_cache(provider=provider, topic=topic)
        if cached is not None:
            details[topic] = cached
    if details:
        attached = dict(payload)
        attached["details"] = _remove_non_finite_json_values(details)
        return attached
    return payload


def _empty_screening_hotspot_payload(
    *,
    provider: str,
    provider_used: str = "",
    source_errors: Optional[List[str]] = None,
    message: str = "",
) -> Dict[str, Any]:
    """构造一个"空但形状齐全"的热点列表响应，便于在降级时直接返回。"""
    return {
        "enabled": True,
        "provider": provider,
        "provider_used": provider_used,
        "fallback_used": False,
        "cache_used": False,
        "cached_at": None,
        "source_errors": list(source_errors or []),
        "stale": False,
        "stale_age_hours": None,
        "hotspots": [],
        "hotspot_count": 0,
        "message": message,
    }


def _is_known_eastmoney_hotspot_connectivity_error(exc: BaseException) -> bool:
    """判断异常是否为"东财热点源的瞬时网络层错误"，用于决定是否走降级路径。"""
    retryable_types: List[Any] = [ConnectionError, TimeoutError]
    try:
        import requests

        retryable_types.extend(
            [
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError,
            ]
        )
    except Exception:
        pass
    try:
        import http.client

        retryable_types.extend([http.client.RemoteDisconnected, http.client.IncompleteRead])
    except Exception:
        pass
    try:
        import urllib3.exceptions

        retryable_types.extend(
            [
                urllib3.exceptions.ProtocolError,
                urllib3.exceptions.MaxRetryError,
                urllib3.exceptions.ReadTimeoutError,
                urllib3.exceptions.ConnectTimeoutError,
            ]
        )
    except Exception:
        pass

    retryable_tuple = tuple(retryable_types)
    pending: List[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        if isinstance(current, retryable_tuple):
            return True
        message = f"{current.__class__.__name__}: {current}".lower()
        if any(marker in message for marker in DSA_SCREENING_HOTSPOT_CONNECTIVITY_ERROR_MARKERS):
            return True
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            pending.append(cause)
        if isinstance(context, BaseException):
            pending.append(context)
    return False


def _should_return_eastmoney_hotspot_unavailable(provider_arg: Any, exc: BaseException) -> bool:
    """仅当 provider 是 DSA 东财热点且是已知网络层错误时，返回"不可用"提示。"""
    return isinstance(provider_arg, DsaEastMoneyHotspotProvider) and _is_known_eastmoney_hotspot_connectivity_error(exc)


def _has_degraded_eastmoney_hotspot_failure(provider_arg: Any, source_errors: List[str]) -> bool:
    """通过 ``source_errors`` 字符串判断东财热点是否已经处于"降级失败"状态。"""
    if not isinstance(provider_arg, DsaEastMoneyHotspotProvider):
        return False
    for source_error in source_errors:
        if source_error == DSA_SCREENING_HOTSPOT_UNAVAILABLE_CODE:
            return True
        if _is_known_eastmoney_hotspot_connectivity_error(RuntimeError(source_error)):
            return True
    return False


class ScreeningStrategyResponse(BaseModel):
    """对外暴露的选股策略条目（Pydantic 模型）。"""

    id: str
    name: str = ""
    title: str = ""
    description: str = ""
    category: str = ""
    tag: str = ""
    tags: List[str] = Field(default_factory=list)
    market_scope: List[str] = Field(default_factory=list)
    market: str = ""
    analysis_skills: List[str] = Field(default_factory=list)


class ScreeningService:
    """统一调度 DSA 自有能力与内置选股引擎的选股服务门面。"""

    def __init__(
        self,
        config: Config,
        db_manager: Optional[DatabaseManager] = None,
        user_id: Optional[int] = None,
    ):
        """注入全局配置、历史数据库与当前用户 ID，构造一个轻量的服务实例。"""
        self.config = config
        self.db_manager = db_manager
        self.user_id = user_id

    def status(self) -> Dict[str, Any]:
        """汇总引擎可用性、版本、策略数量、来源健康度等状态信息。"""
        engine_status, available, diagnostics = _get_screening_status_snapshot()
        payload = {
            # 选股是平台内置能力，不是用户可配置的开关。
            "enabled": True,
            "available": available,
            "engine": engine_status.get("engine") or "builtin",
            "contract_version": engine_status.get("contract_version"),
            "version": engine_status.get("version"),
            "strategy_count": engine_status.get("strategy_count"),
            "reference_project": engine_status.get("reference_project"),
            "reference_revision": engine_status.get("reference_revision"),
        }
        source_health = _get_screening_source_health_snapshot()
        if source_health:
            payload["source_health"] = source_health
        if diagnostics:
            payload["diagnostics"] = diagnostics
        return payload

    def strategies(self) -> Dict[str, Any]:
        """返回当前可用的选股策略列表。"""
        _ensure_screening_available_for_use()
        strategies = _list_strategies()
        return {
            "enabled": True,
            "strategies": strategies,
            "strategy_count": len(strategies),
        }

    def history(
        self,
        *,
        limit: int = 20,
        strategy: str = "",
        market: str = "",
    ) -> Dict[str, Any]:
        """从持久化层拉取历史选股运行记录，可按策略/市场过滤。"""
        db_manager = self._require_history_database()
        runs = db_manager.list_screening_runs(
            limit=limit,
            strategy=_env_text(strategy) or None,
            market=_env_text(market) or None,
            user_id=self.user_id,
        )
        return {
            "enabled": True,
            "runs": runs,
            "run_count": len(runs),
        }

    def history_detail(self, run_id: str) -> Dict[str, Any]:
        """读取单次历史选股运行的完整结果。"""
        db_manager = self._require_history_database()
        run = db_manager.get_screening_run(run_id, user_id=self.user_id)
        if run is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "screening_run_not_found",
                    "message": f"选股运行 {run_id} 不存在。",
                },
            )
        return {"enabled": True, **run}

    def source_history(self, *, limit: int = 100) -> Dict[str, Any]:
        """把最近若干次选股运行按"数据源 + 错误"维度做聚合统计。"""
        db_manager = self._require_history_database()
        runs = db_manager.list_screening_runs(limit=limit, user_id=self.user_id)
        return _summarize_screening_source_history(runs)

    def _require_history_database(self) -> DatabaseManager:
        """确保实例已注入数据库管理器，否则抛出 503 错误。"""
        if self.db_manager is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "screening_history_unavailable",
                    "message": "DSA 数据库未注入，无法读取选股运行历史。",
                },
            )
        return self.db_manager

    def hotspots(
        self,
        *,
        provider: str = "",
        top: int = 12,
        refresh: bool = False,
        include_details: bool = False,
    ) -> Dict[str, Any]:
        """返回当前热点题材列表，支持缓存复用与详情预取。"""
        _ensure_screening_available_for_use()
        provider_name, provider_arg = _resolve_hotspot_provider(provider)
        top_count = max(1, min(int(top or 12), 50))
        cache_top_count = max(top_count, DSA_SCREENING_MIN_HOTSPOT_CACHE_COUNT)
        if not refresh:
            cached = _load_screening_hotspot_cache(provider=provider_name, top=top_count)
            if cached is not None:
                return _attach_cached_hotspot_details(cached, provider=provider_name, top=top_count) if include_details else cached

        try:
            # 热点 provider 的入参显式传入；网络 I/O 期间不持全局 env 锁，
            # 否则先开始的热点刷新会把并发的选股请求阻塞到完整超时。
            raw = screening_hotspot.discover_hotspots(
                provider=provider_arg,
                top=cache_top_count,
                history_path=_screening_hotspot_history_path(),
                fallback_cache_path=_screening_hotspot_cache_path(),
            )
        except HTTPException:
            raise
        except Exception as exc:
            cached = _load_screening_hotspot_cache(
                provider=provider_name,
                top=top_count,
                allow_stale=True,
            )
            if cached is not None:
                errors = list(cached.get("source_errors") or [])
                errors.append(f"live refresh failed: {exc}")
                cached["source_errors"] = errors
                cached["fallback_used"] = True
                cached["cache_used"] = True
                return _attach_cached_hotspot_details(cached, provider=provider_name, top=top_count) if include_details else cached
            if not _should_return_eastmoney_hotspot_unavailable(provider_arg, exc):
                diagnostics = _log_unexpected_screening_exception("hotspot_refresh", exc)
                raise HTTPException(
                    status_code=424,
                    detail={
                        "error": "screening_hotspot_refresh_failed",
                        "message": f"Screening hotspot refresh failed: {exc}",
                        "diagnostics": diagnostics,
                    },
                ) from exc
            logger.warning("Screening hotspot live refresh failed without cache: %s", exc)
            return _empty_screening_hotspot_payload(
                provider=provider_name,
                provider_used=type(provider_arg).__name__,
                source_errors=[DSA_SCREENING_HOTSPOT_UNAVAILABLE_CODE],
                message=DSA_SCREENING_HOTSPOT_UNAVAILABLE_MESSAGE,
            )

        items = _remove_non_finite_json_values(_to_plain(raw))
        if not isinstance(items, list):
            items = []
        cache_rows = items[:cache_top_count]
        source_errors = _list_text_values(getattr(raw, "source_errors", []))
        direct_hotspot_fallback_used = False
        if isinstance(provider_arg, DsaEastMoneyHotspotProvider) and _hotspot_rows_are_thin(cache_rows, top=cache_top_count):
            try:
                direct_hotspots = provider_arg.hotspot_rows(top=cache_top_count)
            except Exception as exc:
                logger.warning("Screening DSA direct hotspot fallback failed: %s", exc)
                direct_hotspots = []
                source_errors.append(f"dsa_direct_hotspots_failed: {exc}")
            if len(direct_hotspots) > len(cache_rows):
                cache_rows = direct_hotspots
                direct_hotspot_fallback_used = True
                source_errors.append("Screening hotspot rows were thin; used DSA EastMoney board-change rows.")
        if isinstance(provider_arg, DsaEastMoneyHotspotProvider) and cache_rows:
            cache_rows = _enrich_hotspot_rows_from_provider(cache_rows, provider_arg, top=cache_top_count)
        selected = cache_rows[:top_count]
        if not selected and source_errors:
            cached = _load_screening_hotspot_cache(
                provider=provider_name,
                top=top_count,
                allow_stale=True,
            )
            if cached is not None:
                errors = list(cached.get("source_errors") or [])
                errors.extend(source_errors)
                cached["source_errors"] = errors
                cached["fallback_used"] = True
                cached["cache_used"] = True
                return _attach_cached_hotspot_details(cached, provider=provider_name, top=top_count) if include_details else cached
            if _has_degraded_eastmoney_hotspot_failure(provider_arg, source_errors):
                return _empty_screening_hotspot_payload(
                    provider=provider_name,
                    provider_used=str(getattr(raw, "provider_used", "") or type(provider_arg).__name__),
                    source_errors=[DSA_SCREENING_HOTSPOT_UNAVAILABLE_CODE],
                    message=DSA_SCREENING_HOTSPOT_UNAVAILABLE_MESSAGE,
                )

        payload = {
            "enabled": True,
            "provider": provider_name,
            "provider_used": "dsa_eastmoney_board_change" if direct_hotspot_fallback_used else str(getattr(raw, "provider_used", "")),
            "fallback_used": direct_hotspot_fallback_used or bool(getattr(raw, "fallback_used", False)),
            "cache_used": False,
            "cached_at": None,
            "source_errors": source_errors,
            "stale": bool(getattr(raw, "stale", False)),
            "stale_age_hours": getattr(raw, "stale_age_hours", None),
            "hotspots": selected,
            "hotspot_count": len(selected),
        }
        if selected and include_details:
            payload = self._prefetch_hotspot_details(payload, provider=provider_name, refresh=False)
        if selected:
            cache_payload = dict(payload)
            cache_payload["hotspots"] = cache_rows
            cache_payload["hotspot_count"] = len(cache_rows)
            _write_screening_hotspot_cache(cache_payload)
        return payload

    def _prefetch_hotspot_details(self, payload: Dict[str, Any], *, provider: str, refresh: bool) -> Dict[str, Any]:
        """给热点列表批量预取前 N 个题材的详情缓存，减少前端详情页等待。"""
        rows = payload.get("hotspots")
        if not isinstance(rows, list) or not rows:
            return payload
        details = dict(payload.get("details") if isinstance(payload.get("details"), dict) else {})
        source_errors = _list_text_values(payload.get("source_errors"))
        for row in rows[:DSA_SCREENING_HOTSPOT_PREFETCH_DETAIL_COUNT]:
            topic = _hotspot_topic_from_row(row)
            if not topic or (topic in details and not refresh):
                continue
            try:
                details[topic] = self.hotspot_detail(topic=topic, provider=provider, refresh=refresh)
            except HTTPException as exc:
                source_errors.append(f"hotspot_detail_prefetch_failed:{topic}:{exc.detail}")
            except Exception as exc:
                source_errors.append(f"hotspot_detail_prefetch_failed:{topic}:{exc}")
        attached = dict(payload)
        if details:
            attached["details"] = _remove_non_finite_json_values(details)
        if source_errors:
            attached["source_errors"] = source_errors
        return attached

    def hotspot_detail(
        self,
        *,
        topic: str,
        provider: str = "",
        refresh: bool = False,
        include_search: bool = False,
    ) -> Dict[str, Any]:
        """返回单题材的完整详情；可按需附加搜索增强。"""
        _ensure_screening_available_for_use()
        topic_text = _env_text(topic)
        if not topic_text:
            raise HTTPException(
                status_code=400,
                detail={"error": "screening_hotspot_topic_required", "message": "热点题材名称不能为空。"},
            )
        provider_name, provider_arg = _resolve_hotspot_provider(provider)
        if not isinstance(provider_arg, DsaEastMoneyHotspotProvider):
            provider_arg = DsaEastMoneyHotspotProvider()
        cached = None if refresh else _load_screening_hotspot_detail_cache(provider=provider_name, topic=topic_text)
        if cached is not None:
            if not include_search:
                return cached
            return _with_hotspot_search_augmentation(cached, topic=topic_text)
        normalized: Dict[str, Any] = {}
        hotspot_helper_error: str = ""
        try:
            try:
                get_hotspot_detail = screening_hotspot.get_hotspot_detail
            except Exception:
                get_hotspot_detail = None
            if callable(get_hotspot_detail) and type(provider_arg) is DsaEastMoneyHotspotProvider:
                try:
                    detail = get_hotspot_detail(
                        topic_text,
                        provider=provider_arg,
                        top_stocks=30,
                        history_path=_screening_hotspot_history_path(),
                        fallback_cache_path=_screening_hotspot_cache_path(),
                    )
                    normalized = _normalize_screening_hotspot_detail(
                        detail,
                        provider=provider_name,
                        requested_topic=topic_text,
                    )
                    normalized = _merge_provider_hotspot_route_fallback(
                        normalized,
                        provider=provider_arg,
                        topic=topic_text,
                    )
                except Exception as exc:
                    hotspot_helper_error = f"{exc}"
                    logger.warning(
                        "Screening hotspot helper fallback to provider for topic=%s: %s",
                        topic_text,
                        hotspot_helper_error,
                    )
            else:
                normalized = provider_arg.hotspot_detail(topic_text)
            if not normalized:
                normalized = provider_arg.hotspot_detail(topic_text)
        except Exception as exc:
            stale_cached = _load_screening_hotspot_detail_cache(
                provider=provider_name,
                topic=topic_text,
                allow_stale=True,
            )
            if stale_cached is not None:
                source_errors = _list_text_values(stale_cached.get("source_errors"))
                source_errors.append(f"screening_hotspot_detail_stale_cache: {exc}")
                stale_cached["source_errors"] = source_errors
                stale_cached["fallback_used"] = True
                if include_search:
                    return _with_hotspot_search_augmentation(stale_cached, topic=topic_text)
                return stale_cached
            raise HTTPException(
                status_code=424,
                detail={"error": "screening_hotspot_detail_failed", "message": f"Screening hotspot detail failed: {exc}"},
            ) from exc
        if hotspot_helper_error:
            source_errors = _list_text_values(normalized.get("source_errors"))
            source_errors.append(f"screening_hotspot_detail_fallback: {hotspot_helper_error}")
            normalized["source_errors"] = source_errors
            normalized["fallback_used"] = True
            normalized["provider"] = provider_name
        normalized = _ensure_hotspot_detail_compat_fields(normalized)
        normalized["enabled"] = True
        normalized["provider"] = provider_name
        base_detail = _remove_non_finite_json_values(
            _strip_hotspot_search_augmentation(normalized)
        )
        _write_screening_hotspot_detail_cache(
            provider=provider_name,
            topic=topic_text,
            payload=base_detail,
        )
        if include_search:
            return _with_hotspot_search_augmentation(base_detail, topic=topic_text)
        return base_detail

    def screen(
        self,
        *,
        strategy: str,
        market: str,
        max_results: int,
        selection_seed: str = "",
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> Dict[str, Any]:
        """执行一次完整的选股流程并返回归一化后的结果。

        Args:
            strategy: 策略 ID（与 ``strategies()`` 返回的条目对应）。
            market: 目标市场代码（如 A 股/港股/美股）。
            max_results: 最终候选数量上限。
            selection_seed: 用于在多次筛选间稳定排序的随机种子。
            progress_callback: 进度回调，签名为 ``(progress: int, message: str)``。

        Returns:
            选股响应字典，含 ``candidates``、``warnings``、LLM 字段、增广信息等。
        """
        _ensure_screening_available_for_use()
        _ensure_supported_market(market)
        _ensure_supported_strategy(strategy)

        try:
            raw = _call_screening_screen(
                strategy,
                market,
                max_results,
                self.config,
                selection_seed=selection_seed,
                progress_callback=progress_callback,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "screening_screen_rejected", "message": str(exc)},
            ) from exc
        except (TypeError, KeyError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "screening_invalid_input", "message": f"Screening 参数非法：{exc}"},
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=424,
                detail={"error": "screening_screen_failed", "message": f"Screening 选股运行失败：{exc}"},
            ) from exc

        raw_data = _to_plain(raw)
        if not isinstance(raw_data, dict):
            raw_data = {"candidates": raw_data}
        raw_data = _remove_non_finite_json_values(raw_data)

        candidates = _normalize_candidates(raw_data)
        selected = candidates[:max_results]
        _emit_screening_progress(
            progress_callback,
            92,
            "正在补充入选股票的新闻与事件",
        )
        selected, dsa_enrichment = _enrich_candidates_with_dsa(selected)
        warnings = _collect_screening_warning_messages(raw_data)
        response = {
            "enabled": True,
            "candidates": selected,
            "candidate_count": len(selected),
            "run_id": raw_data.get("run_id") or uuid.uuid4().hex,
            "strategy": raw_data.get("strategy") or strategy,
            "market": raw_data.get("market") or market,
            "snapshot_count": raw_data.get("snapshot_count"),
            "snapshot_source": raw_data.get("snapshot_source") or "",
            "after_filter_count": raw_data.get("after_filter_count"),
            "llm_ranked": raw_data.get("llm_ranked"),
            "llm_market_view": raw_data.get("llm_market_view") or "",
            "llm_selection_logic": raw_data.get("llm_selection_logic") or "",
            "llm_portfolio_risk": raw_data.get("llm_portfolio_risk") or "",
            "llm_coverage": raw_data.get("llm_coverage"),
            "llm_parse_errors": _list_text_values(raw_data.get("llm_parse_errors")),
            "llm_model_used": raw_data.get("llm_model_used") or "",
            "llm_attempted_models": _list_text_values(raw_data.get("llm_attempted_models")),
            "llm_failure_reason": raw_data.get("llm_failure_reason") or "",
            "ranking_mode": raw_data.get("ranking_mode") or (
                "llm" if raw_data.get("llm_ranked") else "factor"
            ),
            "degradation": _list_text_values(raw_data.get("degradation")),
            "warnings": warnings,
            "source_errors": _list_text_values(raw_data.get("source_errors")),
            "dsa_enrichment": dsa_enrichment,
            "deep_analysis_requested": raw_data.get("deep_analysis_requested"),
            "post_analyzers": raw_data.get("post_analyzers") or [],
            "daily_enriched": raw_data.get("daily_enriched"),
            "daily_enrich_count": raw_data.get("daily_enrich_count"),
            "risk_enabled": raw_data.get("risk_enabled"),
            "portfolio_diversity_enabled": raw_data.get("portfolio_diversity_enabled"),
            "portfolio_concentration_notes": raw_data.get("portfolio_concentration_notes") or [],
            "result_variant_applied": bool(raw_data.get("result_variant_applied")),
            "result_variant_pool_size": raw_data.get("result_variant_pool_size") or 0,
            "result_variant_rotated_slots": raw_data.get("result_variant_rotated_slots") or 0,
        }
        if self.db_manager is not None:
            self.db_manager.save_screening_run(response, user_id=self.user_id)
        return response


def _emit_screening_progress(
    callback: Callable[[int, str], None] | None,
    progress: int,
    message: str,
) -> None:
    """包装进度回调，保证回调异常不会中断选股流程。"""
    if callback is None:
        return
    try:
        callback(progress, message)
    except Exception as exc:  # noqa: BLE001 - progress reporting must not fail screening.
        logger.debug("Screening service progress callback failed: %s", exc)


def _normalize_screening_hotspot_detail(detail: Any, *, provider: str, requested_topic: str) -> Dict[str, Any]:
    """把上游返回的题材详情规整为前端期望的标准字段形状。"""
    raw_value = _remove_non_finite_json_values(_to_plain(detail))
    raw: Dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
    summary_value = raw.get("summary")
    summary: Dict[str, Any] = summary_value if isinstance(summary_value, dict) else {}
    stocks_value = raw.get("stocks")
    leader_stocks_value = raw.get("leader_stocks")
    stocks: List[Any] = stocks_value if isinstance(stocks_value, list) else []
    leader_stocks: List[Any] = leader_stocks_value if isinstance(leader_stocks_value, list) else []
    timeline_value = raw.get("timeline")
    timeline: List[Any] = timeline_value if isinstance(timeline_value, list) else []
    route_value = raw.get("route")
    route: List[Any] = route_value if isinstance(route_value, list) and route_value else _hotspot_timeline_to_route(timeline)
    source_errors = _list_text_values(raw.get("source_errors") or summary.get("source_errors"))
    topic = _env_text(summary.get("topic") or raw.get("topic") or requested_topic)
    canonical_topic = _env_text(summary.get("canonical_topic") or raw.get("canonical_topic"))
    name = _env_text(summary.get("name") or raw.get("name") or canonical_topic or topic)
    quality_status = _env_text(summary.get("quality_status") or raw.get("quality_status"))
    missing_fields = _list_text_values(summary.get("missing_fields") or raw.get("missing_fields"))
    summary_text_value = raw.get("summary")
    summary_text = (
        summary_text_value
        if isinstance(summary_text_value, str)
        else _build_screening_hotspot_summary_text(summary, topic=topic, canonical_topic=canonical_topic)
    )
    normalized = _ensure_hotspot_detail_compat_fields({
        "enabled": True,
        "provider": provider,
        "topic": topic,
        "name": name,
        "canonical_topic": canonical_topic,
        "aliases": _list_text_values(summary.get("aliases") or raw.get("aliases")),
        "summary": summary_text,
        "summary_detail": summary,
        "route": route,
        "timeline": timeline,
        "stocks": stocks,
        "leader_stocks": leader_stocks,
        "source_errors": source_errors,
        "quality_status": quality_status,
        "missing_fields": missing_fields,
        "fallback_used": bool(summary.get("fallback_used") or raw.get("fallback_used") or False),
        "stale": bool(summary.get("stale") or raw.get("stale") or False),
        "stale_age_hours": summary.get("stale_age_hours") or raw.get("stale_age_hours"),
        "resolver_candidates": _list_dict_values(summary.get("resolver_candidates") or raw.get("resolver_candidates")),
    })
    # 仅含领涨股的"降级响应"是预览，不算完整成份股统计。
    if "live_stocks" in missing_fields:
        normalized["stock_count"] = 0
    return normalized


def _list_text_values(value: Any) -> List[str]:
    """把任意输入归一化为只含非空字符串的列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = _env_text(value)
        return [text] if text else []
    if not isinstance(value, list):
        text = _env_text(value)
        return [text] if text else []
    return [text for item in value if (text := _env_text(item))]


def _list_dict_values(value: Any) -> List[Dict[str, Any]]:
    """过滤并返回仅包含字典元素的列表。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _hotspot_timeline_to_route(timeline: List[Any]) -> List[Dict[str, Any]]:
    """把原始 timeline 转换成可直接展示给前端的 route 列表。"""
    route: List[Dict[str, Any]] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        title = _env_text(item.get("title"))
        if not title:
            continue
        date = _env_text(item.get("date") or item.get("published_at"))
        source = _env_text(item.get("source")) or "screening_timeline"
        route.append({
            "title": title,
            "description": f"{date}：{title}" if date else title,
            "source": source,
            "url": _env_text(item.get("url")),
            "published_at": date,
        })
    if route:
        return route
    return [{
        "title": "等待发酵",
        "description": "暂未获取到明确催化事件，可继续观察涨跌幅、成交额和核心个股联动。",
        "source": "fallback",
    }]


def _merge_provider_hotspot_route_fallback(
    normalized: Dict[str, Any],
    *,
    provider: "DsaEastMoneyHotspotProvider",
    topic: str,
) -> Dict[str, Any]:
    """当主路径拿不到足够的事件 route 时，用 provider 直拉做兜底。"""
    if _has_meaningful_hotspot_route(normalized.get("route")):
        return normalized
    try:
        provider_detail = provider.hotspot_detail(topic)
    except Exception as exc:
        logger.warning(
            "Screening provider route fallback failed for %s; keeping engine detail route: %s",
            topic,
            exc,
        )
        return normalized

    raw_value = _remove_non_finite_json_values(_to_plain(provider_detail))
    raw: Dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
    provider_route = raw.get("route")
    if _has_meaningful_hotspot_route(provider_route):
        normalized["route"] = provider_route
        provider_timeline = raw.get("timeline")
        if not normalized.get("timeline") and isinstance(provider_timeline, list):
            normalized["timeline"] = provider_timeline
        return normalized

    provider_timeline = raw.get("timeline")
    if isinstance(provider_timeline, list) and provider_timeline:
        provider_timeline_route = _hotspot_timeline_to_route(provider_timeline)
        if _has_meaningful_hotspot_route(provider_timeline_route):
            normalized["route"] = provider_timeline_route
            normalized["timeline"] = provider_timeline
    return normalized


def _has_meaningful_hotspot_route(route: Any) -> bool:
    """判断 route 是否携带真实可展示的信息（排除"等待发酵"占位项）。"""
    if not isinstance(route, list):
        return False
    for item in route:
        if not isinstance(item, dict):
            continue
        title = _env_text(item.get("title"))
        description = _env_text(item.get("description"))
        source = _env_text(item.get("source"))
        if not title and not description:
            continue
        if source == "fallback" and title == "等待发酵":
            continue
        return True
    return False


def _build_screening_hotspot_summary_text(summary: Dict[str, Any], *, topic: str, canonical_topic: str) -> str:
    """把 summary 字典拼成一句话简介，用于无文本 summary 时的兜底展示。"""
    display_topic = canonical_topic or topic
    heat = _safe_float(summary.get("heat_score"))
    stage = _env_text(summary.get("stage"))
    leaders = summary.get("leaders") if isinstance(summary.get("leaders"), list) else []
    parts = [display_topic]
    if heat is not None:
        parts.append(f"热度 {heat:.1f}")
    if stage:
        parts.append(f"阶段 {stage}")
    if leaders:
        parts.append("核心股 " + "、".join(_env_text(item) for item in leaders[:3] if _env_text(item)))
    return "，".join(part for part in parts if part) + "。"


def _ensure_screening_available_for_use() -> None:
    """检查选股引擎是否可用，不可用时抛出带诊断信息的 424 异常。"""
    _, available, diagnostics = _get_screening_status_snapshot()
    if available:
        return
    normalized_diagnostics = _include_screening_diagnostic_suffix(diagnostics)
    raise _screening_unavailable_exception(
        "选股功能初始化失败，请检查策略文件、依赖和服务端日志。",
        diagnostics=normalized_diagnostics,
    )


def _include_screening_diagnostic_suffix(
    diagnostics: Optional[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    """为诊断字典补上默认的 ``resolution`` / ``message`` 字段，方便前端呈现。"""
    if diagnostics is None:
        return None
    normalized = dict(diagnostics)
    normalized.setdefault("resolution", "builtin_engine")
    normalized.setdefault(
        "message",
        "请检查后端日志、策略资源和基础数据依赖。",
    )
    return normalized


def _get_screening_status_snapshot() -> Tuple[Dict[str, Any], bool, Optional[Dict[str, str]]]:
    """探测选股引擎状态，返回 ``(engine_status, available, diagnostics)`` 三元组。"""
    try:
        engine_status = _call_screening_status()
    except HTTPException as exc:
        return {}, False, _extract_screening_diagnostics(exc)
    except Exception as exc:
        diagnostics = _log_unexpected_screening_exception("status_probe", exc)
        return {}, False, diagnostics

    return engine_status, _is_engine_available(engine_status), None


def _get_screening_source_health_snapshot() -> Dict[str, Any]:
    """汇总选股相关模块（snapshot/daily）的健康度快照，用于状态页展示。"""
    health: Dict[str, Any] = {}
    for module_name, key, function_name in (
        ("src.services.screening.snapshot", "snapshot", "snapshot_source_health_snapshot"),
        ("src.services.screening.daily", "daily", "daily_source_health_snapshot"),
    ):
        try:
            module = importlib.import_module(module_name)
            snapshot_func = getattr(module, function_name, None)
            if callable(snapshot_func):
                snapshot = _remove_non_finite_json_values(_to_plain(snapshot_func()))
                if snapshot:
                    health[key] = snapshot
        except Exception as exc:
            logger.debug("Screening %s source health snapshot unavailable: %s", key, exc)
    return health


def _is_engine_available(engine_status: Any) -> bool:
    """根据 engine_status 字典判断引擎是否可用（缺字段时视为可用）。"""
    if isinstance(engine_status, dict):
        return bool(engine_status.get("available", True))
    return True


def _call_screening_status() -> Dict[str, Any]:
    """读取内置选股引擎的版本与策略数量，构造对外可见的状态字典。"""
    try:
        strategy_count = len(load_screening_strategies())
    except Exception as exc:
        diagnostics = _log_unexpected_screening_exception("strategy_load", exc)
        raise _screening_unavailable_exception(
            f"选股功能状态检查失败：{exc}",
            diagnostics=diagnostics,
        ) from exc
    return {
        "available": True,
        "engine": "builtin",
        "version": SCREENING_VERSION,
        "contract_version": SCREENING_CONTRACT_VERSION,
        "strategy_count": strategy_count,
        "reference_project": REFERENCE_PROJECT,
        "reference_revision": REFERENCE_REVISION,
    }


def _screening_unavailable_exception(
    message: str,
    *,
    diagnostics: Optional[Dict[str, str]] = None,
) -> HTTPException:
    """构造一个统一的"选股功能不可用" HTTPException（424 状态码）。"""
    detail: Dict[str, Any] = {"error": "screening_unavailable", "message": message}
    if diagnostics:
        detail["diagnostics"] = diagnostics
    return HTTPException(status_code=424, detail=detail)


def _log_unexpected_screening_exception(stage: str, exc: BaseException) -> Dict[str, str]:
    """记录一次未预期的选股异常，并返回给前端展示的极简诊断字典。"""
    logger.warning("Unexpected Screening %s failure: %s", stage, exc, exc_info=exc.__traceback__ is not None)
    return {
        "reason": "unexpected_exception",
        "stage": stage,
        "error_type": exc.__class__.__name__,
    }


def _extract_screening_diagnostics(exc: HTTPException) -> Optional[Dict[str, str]]:
    """从 ``HTTPException.detail`` 里安全地抽取诊断字典。"""
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    diagnostics = detail.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    return {str(key): str(value) for key, value in diagnostics.items()}


def _list_strategies() -> List[Dict[str, Any]]:
    """返回归一化后的所有选股策略条目；返回结构非法时抛出 424。"""
    raw = _to_plain(load_screening_strategies())
    if not isinstance(raw, list):
        raise HTTPException(
            status_code=424,
            detail={"error": "screening_invalid_result", "message": "选股策略列表结构非法。"},
        )

    normalized: List[Dict[str, Any]] = []
    for item in raw:
        strategy = _normalize_strategy(item)
        if not strategy.get("id"):
            continue
        normalized.append(strategy)
    return normalized


def _normalize_strategy(raw: Any) -> Dict[str, Any]:
    """把引擎给出的策略条目转成 ``ScreeningStrategyResponse`` 形状。"""
    item = _to_plain(raw)
    if isinstance(item, str):
        return _strategy_model(id=item, name=item, title=item)
    if not isinstance(item, dict):
        value = str(item)
        return _strategy_model(id=value, name=value, title=value)

    tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    market_scope = item.get("market_scope") or item.get("marketScope") or []
    if not isinstance(market_scope, list):
        market_scope = [str(market_scope)] if market_scope else []

    strategy_id = str(
        item.get("id")
        or item.get("strategy")
        or item.get("strategy_id")
        or item.get("name")
        or "",
    )
    name = str(item.get("display_name") or item.get("name") or item.get("title") or strategy_id)
    category = str(item.get("category") or item.get("tag") or "")
    return _strategy_model(
        id=strategy_id,
        name=name,
        title=str(item.get("title") or name),
        description=str(item.get("description") or ""),
        category=category,
        tag=str(item.get("tag") or category),
        tags=[str(tag) for tag in tags],
        market_scope=[str(market) for market in market_scope],
        market=str(item.get("market") or item.get("market_id") or ""),
        analysis_skills=_list_text_values(
            item.get("analysis_skills") or item.get("analysisSkills")
        ),
    )


def _strategy_model(**kwargs: Any) -> Dict[str, Any]:
    """通过 Pydantic 模型生成一份"可序列化"的策略字典。"""
    normalized = ScreeningStrategyResponse(**kwargs)
    try:
        return normalized.model_dump()
    except AttributeError:
        return normalized.dict()


def _summarize_screening_source_history(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把若干次选股运行按"数据源 + 错误"维度聚合，给状态页呈现稳定度。"""
    source_stats: Dict[str, Dict[str, Any]] = {}
    fallback_runs = 0

    def source_entry(source: str) -> Dict[str, Any]:
        """取某个数据源（或错误归因源）的统计槽位，不存在则初始化。"""
        return source_stats.setdefault(
            source,
            {
                "selected_runs": 0,
                "error_count": 0,
                "last_seen_at": None,
                "error_samples": [],
            },
        )

    for run in runs:
        created_at = run.get("created_at")
        selected_source = _env_text(run.get("snapshot_source")) or "unknown"
        selected = source_entry(selected_source)
        selected["selected_runs"] += 1
        if not selected["last_seen_at"]:
            selected["last_seen_at"] = created_at

        errors = _list_text_values(run.get("source_errors"))
        warnings = _collect_screening_warning_messages(run)
        if errors or any("fallback" in warning.lower() or "降级" in warning for warning in warnings):
            fallback_runs += 1
        for error in errors:
            source = _screening_source_from_error(error)
            entry = source_entry(source)
            entry["error_count"] += 1
            if not entry["last_seen_at"]:
                entry["last_seen_at"] = created_at
            samples = entry["error_samples"]
            if error not in samples and len(samples) < 5:
                samples.append(error)

    return {
        "enabled": True,
        "runs_analyzed": len(runs),
        "fallback_runs": fallback_runs,
        "sources": dict(sorted(source_stats.items())),
    }


def _screening_source_from_error(error: str) -> str:
    """从一条错误文本里提取出对应的数据源短名；解析不到时返回 ``unknown``。"""
    text = _env_text(error)
    match = re.search(
        r"(?:snapshot source fallback:\s*)?([a-zA-Z][a-zA-Z0-9_-]{1,31})\s*(?:after\s+\d+\s+attempts)?\s*:",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).lower() if match else "unknown"


def _ensure_supported_strategy(strategy: str) -> None:
    """若引擎返回的策略列表里存在 ``strategy``，保持透传以便支持自定义策略。"""
    strategies = _list_strategies()
    if not strategies:
        return

    ids = {item.get("id") for item in strategies if item.get("id")}
    if strategy in ids:
        return

    # 策略参数由选股引擎执行最终校验，这里保持透传以支持自定义策略。


def _call_screening_screen(
    strategy: str,
    market: str,
    max_results: int,
    config: Config,
    *,
    selection_seed: str = "",
    progress_callback: Callable[[int, str], None] | None = None,
) -> Any:
    """调用内置 pipeline 执行一次选股，并对运行时环境做最小侵入的桥接。"""
    # 环境桥接是进程级的，耗时必须尽量短：先在锁内物化出不可变的 pipeline config，
    # 再释放锁去做网络/LLM 工作。这样热点刷新才能与选股并行。
    with _screening_runtime_env(config, max_results=max_results):
        pipeline_config = ScreeningPipelineConfig.from_env()
        pipeline_context = _build_screening_context(config, max_results=max_results)

    daily_history_fetcher = _build_screening_dsa_daily_history_fetcher()
    with _screening_litellm_headers(config):
        return run_screening_pipeline(
            strategy,
            market=market,
            max_output=max_results,
            use_llm=True,
            selection_seed=selection_seed,
            context=pipeline_context,
            config=pipeline_config,
            progress_callback=progress_callback,
            daily_history_fetcher=daily_history_fetcher,
        )


@contextmanager
def _screening_runtime_env(config: Config, *, max_results: Optional[int] = None) -> Iterator[None]:
    """在上下文范围内向 ``os.environ`` 注入选股所需的运行时变量，退出时恢复。"""
    updates = _build_screening_runtime_env(config, max_results=max_results)
    if not updates:
        yield
        return

    sentinel = object()
    with _SCREENING_RUNTIME_ENV_LOCK:
        previous = {key: os.environ.get(key, sentinel) for key in updates}
        os.environ.update(updates)
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is sentinel:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value  # type: ignore[assignment]


def _build_screening_dsa_daily_history_fetcher() -> Optional[Callable[..., Any]]:
    """构造一个"先走 DSA，失败再走 Screening 默认源"的日线 fetcher。

    返回的闭包持有原 ``fetch_daily_history`` 作为回退；它不会替换模块符号，
    因此并发请求之间不会出现"恢复过期包装"或"嵌套包装"的副作用。
    """
    try:
        daily_module = importlib.import_module("src.services.screening.daily")
    except Exception:
        return None

    original_fetch = getattr(daily_module, "fetch_daily_history", None)
    if not callable(original_fetch):
        return None

    def fetch_daily_history_with_dsa(
        code: str,
        *,
        lookback_days: int = 120,
        source: str = "akshare",
        retries: int = 2,
        cache_dir: str | Path | None = None,
        cache_ttl_seconds: float | None = None,
    ) -> Any:
        """先尝试 DSA 日线，成功则回填来源与缓存属性；失败交由调用方回退默认源。

        返回的 DataFrame 带 ``daily_source`` / ``source_errors`` 等 attrs，
        供上层合并展示数据源健康度。
        """
        try:
            dsa_df, dsa_source = get_dsa_daily_history(code, lookback_days=lookback_days)
            normalized = _normalize_dsa_daily_history(dsa_df)
            if normalized is not None and not normalized.empty:
                resolved_source = f"dsa:{dsa_source}"
                normalized_code = code
                normalize_code = getattr(daily_module, "_normalize_daily_code", None)
                if callable(normalize_code):
                    normalized_code = normalize_code(code)
                normalized.attrs["source"] = resolved_source
                normalized.attrs["daily_source"] = resolved_source
                normalized.attrs["daily_requested_source"] = source
                normalized.attrs["daily_source_order"] = [resolved_source]
                normalized.attrs["daily_source_order_notes"] = []
                normalized.attrs["source_errors"] = []
                normalized.attrs["daily_source_health"] = {}
                if cache_dir is not None:
                    cache_path_builder = getattr(daily_module, "_daily_history_cache_path", None)
                    cache_writer = getattr(daily_module, "_write_daily_history_cache", None)
                    if callable(cache_path_builder) and callable(cache_writer):
                        cache_path = cache_path_builder(
                            cache_dir,
                            code=normalized_code,
                            source=source,
                            lookback_days=int(lookback_days),
                        )
                        cache_writer(
                            cache_path,
                            normalized,
                            code=normalized_code,
                            source=source,
                            lookback_days=int(lookback_days),
                        )
                return normalized
        except Exception as exc:
            logger.warning(
                "Screening DSA daily history fetch failed for %s; falling back to Screening source %s: %s",
                code,
                source,
                exc,
            )
        return original_fetch(
            code,
            lookback_days=lookback_days,
            source=source,
            retries=retries,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
        )

    return fetch_daily_history_with_dsa


def _resolve_screening_snapshot_source_priority(config: Config) -> str:
    """根据是否配置了 Tushare token 返回不同的快照源优先级串。"""
    token = _env_text(getattr(config, "tushare_token", None) or os.getenv("TUSHARE_TOKEN"))
    if token:
        return DSA_SCREENING_SNAPSHOT_SOURCE_PRIORITY_WITH_TUSHARE
    return DSA_SCREENING_SNAPSHOT_SOURCE_PRIORITY


def _build_screening_runtime_env(config: Config, *, max_results: Optional[int] = None) -> Dict[str, str]:
    """构建要注入到 ``os.environ`` 的运行时变量字典。

    仅桥接本次请求/进程范围内的 DSA 解析值，绝不覆盖用户的 .env；
    未配置的渠道/模型也不会被静默迁移。与 LiteLLM provider/model、
    openai-compatible ``api_base`` 与 headers 的注入语义保持一致，
    参考 https://docs.litellm.ai/docs/providers 以及
    https://docs.litellm.ai/docs/proxy/configs#the-model_list-key 。
    """
    env: Dict[str, str] = {}

    def put(key: str, value: Any) -> None:
        """把非空值写入运行时环境变量字典。"""
        text = _env_text(value)
        if text:
            env[key] = text

    def put_default(key: str, value: Any) -> None:
        """仅当系统环境变量未设置时才写入默认值（不覆盖用户已有配置）。"""
        if os.getenv(key) not in (None, ""):
            return
        put(key, value)

    litellm_model, fallback_models = _resolve_screening_llm_models(config)
    put("LITELLM_MODEL", litellm_model)
    if fallback_models:
        put("LITELLM_FALLBACK_MODELS", ",".join(fallback_models))
    put("LITELLM_CONFIG", config.litellm_config_path)
    if os.getenv("LLM_TEMPERATURE") not in (None, ""):
        put("LLM_TEMPERATURE", config.llm_temperature)

    channels = _normalize_dsa_llm_channels(config)
    if channels:
        put("LLM_CHANNELS", ",".join(channel["name"] for channel in channels))
        for channel in channels:
            prefix = channel["name"].upper()
            put(f"LLM_{prefix}_ENABLED", "true")
            put(f"LLM_{prefix}_PROTOCOL", channel.get("protocol"))
            put(f"LLM_{prefix}_API_SURFACE", channel.get("api_surface"))
            put(f"LLM_{prefix}_BASE_URL", channel.get("base_url"))
            put(f"LLM_{prefix}_API_KEYS", ",".join(channel.get("api_keys") or []))
            put(f"LLM_{prefix}_MODELS", ",".join(channel.get("models") or []))
            if channel.get("extra_headers"):
                put(
                    f"LLM_{prefix}_EXTRA_HEADERS",
                    json.dumps(channel.get("extra_headers"), ensure_ascii=False),
                )

    gemini_keys = _dedupe_strings([
        *(config.gemini_api_keys or []),
        *_channel_keys_for_provider(channels, {"gemini", "vertex_ai"}),
    ])
    anthropic_keys = _dedupe_strings([
        *(config.anthropic_api_keys or []),
        *_channel_keys_for_provider(channels, {"anthropic"}),
    ])
    openai_keys = _dedupe_strings([
        *(config.openai_api_keys or []),
        *_channel_keys_for_provider(channels, {"openai"}),
    ])
    deepseek_keys = _dedupe_strings([
        *(config.deepseek_api_keys or []),
        *_channel_keys_for_provider(channels, {"deepseek"}),
    ])

    _put_provider_keys(env, "GEMINI", gemini_keys)
    _put_provider_keys(env, "ANTHROPIC", anthropic_keys)
    _put_provider_keys(env, "OPENAI", openai_keys)
    _put_provider_keys(env, "DEEPSEEK", deepseek_keys)

    put("OPENAI_BASE_URL", config.openai_base_url or _first_channel_base_url(channels, {"openai"}))
    put_default("DAILY_SOURCE", "auto")
    put_default("DAILY_FETCH_RETRIES", str(DSA_SCREENING_DAILY_FETCH_RETRIES))
    put_default("DAILY_FETCH_MAX_WORKERS", "1")
    put("LLM_CANDIDATE_CONTEXT_ENABLED", "false")
    put_default("LLM_CANDIDATE_CONTEXT_PROVIDERS", DSA_SCREENING_CANDIDATE_CONTEXT_PROVIDERS)
    put_default("LLM_CANDIDATE_MULTIPLIER", str(DSA_SCREENING_LLM_CANDIDATE_MULTIPLIER))
    put_default("LLM_MAX_CANDIDATES", str(_resolve_dsa_llm_max_candidates(max_results)))
    put_default("SNAPSHOT_SOURCE_PRIORITY", _resolve_screening_snapshot_source_priority(config))
    screening_data_dir = _resolve_screening_data_dir()
    put_default("SCREENING_DATA_DIR", str(screening_data_dir))
    put_default("SCREENING_HOTSPOT_CACHE_TTL_SEC", str(DSA_SCREENING_HOTSPOT_CACHE_TTL_SECONDS))
    put_default("SCREENING_FALLBACK_SNAPSHOT_PATH", str(screening_data_dir / "snapshot.last_good.json"))
    put_default("SCREENING_DAILY_HISTORY_CACHE_DIR", str(screening_data_dir / "daily_history"))
    put_default("SCREENING_INDUSTRY_PROVIDER_CACHE_DIR", str(screening_data_dir / "industry_provider_cache"))
    return env


def _resolve_hotspot_provider(provider: str) -> Tuple[str, Any]:
    """把入参或环境变量里的热点 provider 名称解析成 ``(name, instance_or_str)``。"""
    requested = (provider or "").strip()
    if requested.lower() == "akshare":
        return requested, DsaEastMoneyHotspotProvider()
    if requested:
        return requested, requested
    configured = (os.getenv("INDUSTRY_PROVIDER") or "").strip()
    if configured.lower() == "akshare":
        return configured, DsaEastMoneyHotspotProvider()
    if configured:
        return configured, configured
    return "akshare", DsaEastMoneyHotspotProvider()


class DsaEastMoneyHotspotProvider:
    """面向选股场景的极简东方财富/同花顺热点 Provider。

    主要能力：
    - 拉取概念/行业板块异动数据，计算热度/趋势/持续性等评分；
    - 解析板块成份股与异动事件，构造题材详情；
    - 内置超时与重试，规避上游瞬时故障。
    """

    # 表明本 provider 已经接入了 DSA 的"可终止调用预算"体系（子类按需使用）
    _screening_source_calls_bounded = True
    # 东方财富板块排行 API 的基础地址
    _BASE_URL = "https://push2.eastmoney.com/api/qt/clist/get"
    # AkShare 类调用的默认子进程超时（秒）
    _AKSHARE_CALL_TIMEOUT_SECONDS = 4.0
    # 直连 HTTP（成份股）的 (connect, read) 默认超时
    _CONSTITUENT_HTTP_TIMEOUT = (1.0, 2.0)
    # 成份股并行 worker 的进程级限流
    _CONSTITUENT_WORKER_SLOTS = threading.BoundedSemaphore(4)
    # 东方财富 clist 接口的通用参数（pn/po/np/ut/fltt/invt/fid/fields）
    _COMMON_PARAMS = {
        "pn": "1",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
    }
    # "宽泛/指数类板块"黑名单关键词，用于剔除大盘/分类板块
    _BROAD_BOARD_KEYWORDS = (
        "融资融券",
        "深股通",
        "沪股通",
        "创业板",
        "昨日",
        "机构重仓",
        "富时罗素",
        "MSCI",
        "标普",
        "上证",
        "深证",
        "中证",
        "HS300",
        "证金",
        "QFII",
        "基金",
        "转融券",
        "预增",
        "预盈",
        "亏损",
        "低价",
        "小盘股",
        "中盘股",
        "百元股",
        "破发",
        "破增发",
        "趋势股",
        "广东板块",
        "江苏板块",
        "浙江板块",
        "上海板块",
        "深圳特区",
        "央国企",
        "国企改革",
        "专精特新",
        "其他",
        "Ⅱ",
        "Ⅲ",
    )
    # 东方财富异动事件码到中文标签的映射
    _CHANGE_EVENT_LABELS = {
        4: "快速拉升",
        8: "快速回落",
        16: "大幅上涨",
        32: "大幅下跌",
        64: "有大笔买入",
        128: "有大笔卖出",
        8193: "火箭发射",
        8194: "高台跳水",
        8201: "大笔买入",
        8202: "大笔卖出",
        8203: "封涨停板",
        8204: "打开涨停板",
        8207: "有打开跌停板",
        8208: "封跌停板",
        8209: "向上缺口",
        8210: "向下缺口",
        8211: "60日新高",
        8212: "60日新低",
        8213: "60日大幅上涨",
        8214: "60日大幅下跌",
        8215: "竞价上涨",
        8216: "竞价下跌",
        8217: "高开",
        8218: "低开",
        8219: "放量",
        8220: "缩量",
        8221: "向上突破",
        8222: "向下破位",
    }
    # 金属类题材关键词到所属产业链分组的映射
    _METAL_TOPIC_GROUPS = {
        "钼": "小金属",
        "钨": "小金属",
        "钴": "小金属",
        "镍": "小金属",
        "锑": "小金属",
        "铟": "小金属",
        "锗": "小金属",
        "铅锌": "工业金属",
        "铜": "工业金属",
        "铝": "工业金属",
        "锡": "工业金属",
        "黄金": "贵金属",
        "白银": "贵金属",
        "贵金属": "贵金属",
    }
    # 同花顺题材名称到官方主题的别名映射，便于归一化
    _THS_TOPIC_ALIASES = {
        "文字媒体": ("文化传媒概念", "文化传媒"),
    }

    def __init__(self) -> None:
        """初始化 session、节流锁与本地缓存结构。"""
        import requests

        self._board_changes_raw_cache: Any = None
        self._board_changes_frame_cache: Any = None
        self._constituent_cache: Dict[Tuple[str, str], Any] = {}
        self._session = requests.Session()
        self._request_lock = threading.RLock()
        self._last_request_ts = 0.0
        self._min_request_interval = 0.25

    @contextmanager
    def _source_call_budget(self) -> Iterator[None]:
        """为一次"板块/成份股/详情"调用应用统一的超时预算。

        AkShare 走 DSA 的可终止子进程、直连 HTTP 走 socket 超时，
        因此不能再套通用守护线程超时。嵌套调用会复用同一 deadline，
        避免每次 fallback 都重新获得完整预算。
        """
        if _DSA_HOTSPOT_CALL_DEADLINE.get() is not None:
            yield
            return
        timeout = parse_source_timeout_seconds(
            "SCREENING_HOTSPOT_CALL_TIMEOUT_SEC",
            default=DSA_SCREENING_HOTSPOT_CALL_TIMEOUT_SECONDS,
        )
        if timeout is None:
            yield
            return
        token = _DSA_HOTSPOT_CALL_DEADLINE.set(time.monotonic() + timeout)
        try:
            yield
        finally:
            _DSA_HOTSPOT_CALL_DEADLINE.reset(token)

    def _remaining_source_timeout(self, fallback: float) -> float:
        """返回剩余预算（秒）；无 deadline 时退回到 ``fallback``。"""
        deadline = _DSA_HOTSPOT_CALL_DEADLINE.get()
        if deadline is None:
            return float(fallback)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("screening hotspot provider call exceeded its configured timeout")
        return remaining

    def _akshare_timeout_seconds(self) -> float:
        """获取 AkShare 调用剩余预算；无 deadline 时使用默认上限。"""
        return self._remaining_source_timeout(self._AKSHARE_CALL_TIMEOUT_SECONDS)

    def _http_timeout(self) -> Tuple[float, float]:
        """计算当前 HTTP 调用的 (connect, read) 超时，受剩余 deadline 影响。"""
        deadline = _DSA_HOTSPOT_CALL_DEADLINE.get()
        if deadline is None:
            return self._CONSTITUENT_HTTP_TIMEOUT
        remaining = self._remaining_source_timeout(sum(self._CONSTITUENT_HTTP_TIMEOUT))
        connect = min(self._CONSTITUENT_HTTP_TIMEOUT[0], max(remaining / 2.0, 0.001))
        read = max(remaining - connect, 0.001)
        return connect, read

    def _sleep_within_source_budget(self, seconds: float) -> None:
        """在 deadline 内安全 sleep；若剩余时间不足则主动抛 TimeoutError。"""
        deadline = _DSA_HOTSPOT_CALL_DEADLINE.get()
        if deadline is not None and self._remaining_source_timeout(seconds) <= seconds:
            raise TimeoutError("screening hotspot provider call exceeded its configured timeout")
        time.sleep(seconds)

    def _eastmoney_get_once(self, url: str, **kwargs: Any) -> Any:
        """单次直连东方财富的 HTTP 请求，自动节流并设置 timeout。"""
        with self._request_lock:
            elapsed = time.monotonic() - self._last_request_ts
            if elapsed < self._min_request_interval:
                self._sleep_within_source_budget(self._min_request_interval - elapsed)
            kwargs["timeout"] = self._http_timeout()
            try:
                return self._session.get(url, **kwargs)
            finally:
                self._last_request_ts = time.monotonic()

    def _eastmoney_get(self, url: str, **kwargs: Any) -> Any:
        """带短重试的东方财富 GET（不延长单次 socket 等待）。"""
        import requests

        retryable_errors = (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError,
        )
        delays = (0.3, 0.8)
        last_error: Optional[BaseException] = None
        for attempt in range(len(delays) + 1):
            try:
                return self._eastmoney_get_once(url, **kwargs)
            except retryable_errors as exc:
                last_error = exc
                if attempt >= len(delays):
                    break
                logger.warning(
                    "Screening EastMoney hotspot request failed; retrying attempt=%s: %s",
                    attempt + 1,
                    exc,
                )
                self._sleep_within_source_budget(delays[attempt])
        assert last_error is not None
        raise last_error

    def stock_board_concept_name_em(self) -> Any:
        """拉取概念板块的热点表（DataFrame），含异动/排行/名称三种 fallback。"""
        with self._source_call_budget():
            frame = self._fetch_board_changes_with_fallback()
            if frame is not None and not frame.empty:
                return frame
            frame = self._fetch_rankings_with_fallback("concept")
            if frame is not None and not frame.empty:
                return frame
            return self._fetch_board_names(source_fs="m:90 t:3 f:!50")

    def stock_board_industry_name_em(self) -> Any:
        """拉取行业板块的热点表（DataFrame）。如果概念异动里有结果则跳过额外请求。"""
        with self._source_call_budget():
            concept_frame = self._fetch_board_changes_with_fallback()
            if concept_frame is not None and not concept_frame.empty:
                import pandas as pd

                return pd.DataFrame()
            frame = self._fetch_rankings_with_fallback("industry")
            if frame is not None and not frame.empty:
                return frame
            return self._fetch_board_names(source_fs="m:90 t:2 f:!50")

    def hotspot_rows(self, *, top: int = 12) -> List[Dict[str, Any]]:
        """返回排序后的热点行字典列表（含热度/趋势/持续性评分）。"""
        import pandas as pd

        with self._source_call_budget():
            frame = self.stock_board_concept_name_em()
        df = pd.DataFrame(frame)
        if df.empty:
            return []
        rows: List[Dict[str, Any]] = []
        for index, row in df.head(max(1, min(top, 50))).iterrows():
            name = _env_text(row.get("name") or row.get("板块名称") or row.get("行业名称") or row.get("名称"))
            if not name:
                continue
            change_pct = _safe_float(row.get("change_pct") or row.get("涨跌幅"))
            event_count = int(_safe_float(row.get("event_count") or row.get("observations")) or 0)
            leader = _env_text(row.get("leader"))
            leaders_raw = row.get("leaders")
            leaders = _list_text_values(leaders_raw) or ([leader] if leader else [])
            heat_score = _safe_float(row.get("heat_score"))
            if heat_score is None:
                heat_score = min(99.0, max(1.0, max(change_pct or 0.0, 0.0) * 9.0 + event_count / 120.0))
            trend_score = _safe_float(row.get("trend_score"))
            if trend_score is None:
                trend_score = self._derive_trend_score(change_pct=change_pct, event_count=event_count)
            persistence_score = _safe_float(row.get("persistence_score"))
            if persistence_score is None:
                persistence_score = self._derive_persistence_score(event_count=event_count)
            stage = _env_text(row.get("stage") or row.get("state")) or self._derive_hotspot_stage(
                change_pct=change_pct,
                event_count=event_count,
            )
            display_name = self._display_hotspot_name(name)
            rows.append({
                "topic": name,
                "name": display_name,
                "theme_group": self._hotspot_group(name),
                "source": "dsa_eastmoney_board_change",
                "rank": len(rows) + 1,
                "change_pct": change_pct,
                "heat_score": round(float(heat_score), 2),
                "trend_score": trend_score,
                "persistence_score": persistence_score,
                "observations": event_count,
                "state": stage,
                "stage": stage,
                "sample_stock_count": int(_safe_float(row.get("sample_stock_count")) or len(leaders)),
                "leaders": leaders,
            })
        return rows

    def stock_board_concept_cons_em(self, symbol: str = "") -> Any:
        """获取概念板块成份股 DataFrame（多源并行，结果按顺序合并）。"""
        with self._source_call_budget():
            cached = self._get_constituent_cache("concept", symbol)
            if cached is not None:
                return cached
            frames = self._fetch_constituent_sources(symbol, source="concept")
            frames.append(self._fallback_constituents(symbol))
            frames.append(self._related_hotspot_constituents(symbol))
            frame = self._merge_constituent_frames(frames)
            self._set_constituent_cache("concept", symbol, frame)
            return frame

    def stock_board_industry_cons_em(self, symbol: str = "") -> Any:
        """获取行业板块成份股 DataFrame（多源并行，结果按顺序合并）。"""
        with self._source_call_budget():
            cached = self._get_constituent_cache("industry", symbol)
            if cached is not None:
                return cached
            frames = self._fetch_constituent_sources(symbol, source="industry")
            frames.append(self._fallback_constituents(symbol))
            frame = self._merge_constituent_frames(frames)
            self._set_constituent_cache("industry", symbol, frame)
            return frame

    def _fetch_constituent_sources(self, topic: str, *, source: str) -> List[Any]:
        """并行抓取各数据源的成份股，避免产生孤儿 worker。

        AkShare 走 DSA 的可终止子进程，HTTP 走 connect/read 超时。
        进程级信号量限制并发上限，且执行器在返回前会 join 所有已 admitted worker。
        """
        fetchers: List[Tuple[str, Callable[[], Any]]] = [
            ("eastmoney", lambda: self._fetch_eastmoney_constituents(topic, source=source)),
        ]
        if source == "concept":
            fetchers.append(("ths", lambda: self._fetch_ths_constituents(topic)))

        source_deadline = _DSA_HOTSPOT_CALL_DEADLINE.get()

        def run(fetch: Callable[[], Any]) -> Any:
            """在线程中执行抓取：恢复调用方的 DSA 截止时间并归还 worker 槽位。"""
            token = (
                _DSA_HOTSPOT_CALL_DEADLINE.set(source_deadline)
                if source_deadline is not None
                else None
            )
            try:
                return fetch()
            finally:
                if token is not None:
                    _DSA_HOTSPOT_CALL_DEADLINE.reset(token)
                self._CONSTITUENT_WORKER_SLOTS.release()

        frames_by_source: Dict[str, Any] = {}
        with ThreadPoolExecutor(
            max_workers=len(fetchers),
            thread_name_prefix="screening-constituents",
        ) as executor:
            futures = {}
            for label, fetch in fetchers:
                if not self._CONSTITUENT_WORKER_SLOTS.acquire(blocking=False):
                    logger.info(
                        "Screening %s constituent source skipped for %s: worker capacity exhausted",
                        label,
                        topic,
                    )
                    continue
                try:
                    futures[executor.submit(run, fetch)] = label
                except BaseException:  # noqa: BLE001 - release capacity if submission fails.
                    self._CONSTITUENT_WORKER_SLOTS.release()
                    raise
            for future in as_completed(futures):
                label = futures[future]
                try:
                    payload = future.result()
                except BaseException as exc:  # noqa: BLE001 - external source failures are isolated.
                    logger.info("Screening %s constituent source failed for %s: %s", label, topic, exc)
                    continue
                if payload is None or bool(getattr(payload, "empty", False)):
                    continue
                frames_by_source[label] = payload
        return [
            frames_by_source[label]
            for label, _fetch in fetchers
            if label in frames_by_source
        ]

    def hotspot_detail(self, topic: str) -> Dict[str, Any]:
        """对外暴露的题材详情入口，受 ``_source_call_budget`` 保护。"""
        with self._source_call_budget():
            return self._hotspot_detail(topic)

    def _hotspot_detail(self, topic: str) -> Dict[str, Any]:
        """构造题材详情的核心逻辑，包含摘要、成份股、事件 route。"""
        try:
            summary = self._find_board_change(topic)
        except Exception as exc:
            logger.warning(
                "Screening board-change summary fetch failed for %s; continuing without summary: %s",
                topic,
                exc,
            )
            summary = {}
        if self._is_industry_hotspot(topic):
            stocks = self._normalize_constituent_records(self.stock_board_industry_cons_em(topic))
        else:
            stocks = self._normalize_constituent_records(self.stock_board_concept_cons_em(topic))
        route = self._build_hotspot_route(topic, summary)
        info = self._fetch_ths_info(topic)
        if info:
            route.append({
                "title": "同花顺板块概况",
                "description": "；".join(f"{key} {value}" for key, value in list(info.items())[:4]),
                "source": "ths_info",
            })
        if not stocks and summary:
            stock_code = _env_text(summary.get("板块异动最频繁个股及所属类型-股票代码"))
            stock_name = _env_text(summary.get("板块异动最频繁个股及所属类型-股票名称"))
            if stock_code or stock_name:
                stocks.append({
                    "code": stock_code,
                    "name": stock_name,
                    "role": "异动核心",
                    "change_pct": None,
                    "hot_stock_score": 60.0,
                })
        return _ensure_hotspot_detail_compat_fields({
            "topic": topic,
            "name": self._display_hotspot_name(topic),
            "canonical_topic": topic,
            "summary": self._build_hotspot_summary(topic, summary),
            "route": route,
            "stocks": stocks[:30],
            "leader_stocks": stocks[:30],
            "stock_count": len(stocks),
            "source_errors": [],
        })

    def _fetch_board_changes(self) -> Any:
        """读取并缓存东方财富"板块异动"原始 DataFrame，附带上评分与排名。"""
        import pandas as pd

        if self._board_changes_frame_cache is not None:
            return self._board_changes_frame_cache.copy()

        df = self._fetch_board_changes_raw()
        if df is None or df.empty:
            return pd.DataFrame()
        rows = []
        for index, row in df.iterrows():
            topic = _env_text(row.get("板块名称"))
            if not topic or self._is_broad_board(topic):
                continue
            change_pct = _safe_float(row.get("涨跌幅"))
            event_count = int(_safe_float(row.get("板块异动总次数")) or 0)
            leader = _env_text(row.get("板块异动最频繁个股及所属类型-股票名称"))
            heat_score = min(99.0, max(1.0, event_count / 120.0 + max(change_pct or 0.0, 0.0) * 9.0))
            trend_score = self._derive_trend_score(change_pct=change_pct, event_count=event_count)
            persistence_score = self._derive_persistence_score(event_count=event_count)
            leaders = [leader] if leader else []
            stage = self._derive_hotspot_stage(change_pct=change_pct, event_count=event_count)
            rows.append({
                "name": topic,
                "change_pct": change_pct,
                "rank": index + 1,
                "heat_score": heat_score,
                "trend_score": trend_score,
                "persistence_score": persistence_score,
                "observations": event_count,
                "state": stage,
                "stage": stage,
                "sample_stock_count": len(leaders),
                "leaders": leaders,
                "leader": leader,
                "event_count": event_count,
            })
        rows.sort(key=lambda item: (item.get("heat_score") or 0, item.get("event_count") or 0), reverse=True)
        frame = pd.DataFrame(rows)
        self._board_changes_frame_cache = frame
        return frame.copy()

    def _fetch_board_changes_raw(self) -> Any:
        """通过 AkShare 拉取板块异动原始 DataFrame，并在内部缓存。"""
        import akshare as ak
        from data_provider.akshare_fetcher import _akshare_call_with_timeout

        if self._board_changes_raw_cache is not None:
            return self._board_changes_raw_cache.copy()
        df = _akshare_call_with_timeout(
            ak.stock_board_change_em,
            timeout=self._akshare_timeout_seconds(),
            call_name="screening.stock_board_change_em",
        )
        self._board_changes_raw_cache = df
        return df.copy() if df is not None else df

    def _fetch_board_changes_with_fallback(self) -> Any:
        """调用 ``_fetch_board_changes`` 并在异常时返回空 DataFrame，便于上游判断降级。"""
        import pandas as pd

        try:
            return self._fetch_board_changes()
        except Exception as exc:
            logger.warning("Screening hotspot board-change fetch failed; falling back to ranking/board names: %s", exc)
            return pd.DataFrame()

    def _is_broad_board(self, name: str) -> bool:
        """判断板块名是否属于"宽泛/指数类"黑名单（用于过滤大盘/分类板块）。"""
        return any(keyword in name for keyword in self._BROAD_BOARD_KEYWORDS)

    def _fetch_rankings(self, source: str) -> Any:
        """通过 DSA fetcher manager 拉取概念/行业板块排行（100 名 Top）。"""
        import pandas as pd
        from data_provider.akshare_fetcher import _akshare_call_with_timeout

        top, _bottom = _akshare_call_with_timeout(
            _fetch_dsa_hotspot_rankings,
            source,
            100,
            timeout=self._akshare_timeout_seconds(),
            call_name=f"screening.dsa_{source}_rankings",
        )
        rows = []
        for index, item in enumerate(top or []):
            name = _env_text((item or {}).get("name"))
            if not name:
                continue
            rows.append({
                "name": name,
                "change_pct": (item or {}).get("change_pct"),
                "rank": index + 1,
            })
        return pd.DataFrame(rows)

    def _fetch_rankings_with_fallback(self, source: str) -> Any:
        """排行抓取的容错版本，失败时返回空 DataFrame。"""
        import pandas as pd

        try:
            return self._fetch_rankings(source)
        except Exception as exc:
            logger.warning("Screening hotspot %s ranking fetch failed; falling back to board names: %s", source, exc)
            return pd.DataFrame()

    def _fetch_board_names(self, *, source_fs: str) -> Any:
        """直接调用东方财富 clist 接口，按 ``fs`` 条件取板块名称列表。"""
        import pandas as pd

        params = dict(self._COMMON_PARAMS)
        params.update({"pz": "100", "fs": source_fs})
        response = self._eastmoney_get(
            self._BASE_URL,
            params=params,
            timeout=self._http_timeout(),
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"},
        )
        response.raise_for_status()
        payload = response.json()
        rows = ((payload.get("data") or {}).get("diff") or []) if isinstance(payload, dict) else []
        normalized = [
            {
                "板块名称": str(row.get("f14") or "").strip(),
                "涨跌幅": row.get("f3"),
                "序号": index + 1,
                "name": str(row.get("f14") or "").strip(),
                "change_pct": row.get("f3"),
                "rank": index + 1,
                "leader": str(row.get("f140") or row.get("f128") or "").strip(),
                "up_count": row.get("f104"),
                "down_count": row.get("f105"),
                "source": "eastmoney_push2_board_spot",
            }
            for index, row in enumerate(rows)
            if str(row.get("f14") or "").strip()
        ]
        return pd.DataFrame(normalized)

    def _find_board_change(self, topic: str) -> Dict[str, Any]:
        """在板块异动表里精确/模糊查找 ``topic`` 对应行，返回首行字典。"""
        df = self._fetch_board_changes_raw()
        if df is None or df.empty:
            return {}
        rows = df[df["板块名称"].astype(str) == topic]
        if rows.empty:
            rows = df[df["板块名称"].astype(str).str.contains(re.escape(topic), case=False, na=False)]
        if rows.empty:
            return {}
        return rows.iloc[0].to_dict()

    def _is_industry_hotspot(self, topic: str) -> bool:
        """判断题材是不是"行业板块"：先看异动表里没有，再去行业接口里确认。"""
        # 东财异动行已经覆盖了"热点概念"；若题材已在异动表中，就避免再多发一次行业请求。
        try:
            concept_frame = self._fetch_board_changes_with_fallback()
            if self._board_frame_contains_topic(concept_frame, topic):
                return False
        except Exception:
            pass
        try:
            frame = self.stock_board_industry_name_em()
        except Exception as exc:
            logger.warning(
                "Screening industry hotspot source check failed for %s; using concept constituents: %s",
                topic,
                exc,
            )
            return False
        return self._board_frame_contains_topic(frame, topic)

    def _derive_trend_score(self, *, change_pct: Optional[float], event_count: int) -> float:
        """根据涨跌幅与异动次数推导 1~99 的"趋势强度"评分。"""
        change_component = max(change_pct or 0.0, 0.0) * 12.0
        event_component = min(event_count / 8.0, 45.0)
        return round(min(99.0, max(1.0, change_component + event_component)), 1)

    def _derive_persistence_score(self, *, event_count: int) -> float:
        """根据异动次数推导 1~99 的"持续性"评分。"""
        return round(min(99.0, max(1.0, event_count / 3.0)), 1)

    def _derive_hotspot_stage(self, *, change_pct: Optional[float], event_count: int) -> str:
        """根据涨跌幅与异动次数给热点打阶段标签（加速发酵/持续发酵/快速拉升/初次异动）。"""
        positive_change = max(change_pct or 0.0, 0.0)
        if event_count >= 180 and positive_change >= 3.0:
            return "加速发酵"
        if event_count >= 90:
            return "持续发酵"
        if positive_change >= 5.0:
            return "快速拉升"
        return "初次异动"

    def _hotspot_group(self, topic: str) -> str:
        """根据题材名匹配金属类题材分组（如小金属/工业金属/贵金属）。"""
        topic_text = _env_text(topic)
        for keyword, group in self._METAL_TOPIC_GROUPS.items():
            if keyword and keyword in topic_text:
                return group
        return ""

    def _display_hotspot_name(self, topic: str) -> str:
        """构造面向前端展示的题材名（自动加上产业链分组前缀）。"""
        topic_text = _env_text(topic)
        group = self._hotspot_group(topic_text)
        if group and topic_text != group:
            return f"{group} · {topic_text}"
        return topic_text

    def _board_frame_contains_topic(self, frame: Any, topic: str) -> bool:
        """在 DataFrame 的多种名称列里查找题材名是否出现。"""
        import pandas as pd

        topic_text = _env_text(topic)
        if not topic_text:
            return False
        df = pd.DataFrame(frame)
        if df.empty:
            return False
        for column in ("name", "板块名称", "行业名称", "名称"):
            if column not in df.columns:
                continue
            values = df[column].map(_env_text)
            if bool((values == topic_text).any()):
                return True
        return False

    def _build_hotspot_summary(self, topic: str, summary: Dict[str, Any]) -> str:
        """把异动摘要拼成一句话简介，给前端卡片做摘要展示。"""
        if not summary:
            return f"{topic} 当前暂无可用的板块异动摘要。"
        change_pct = _safe_float(summary.get("涨跌幅"))
        event_count = int(_safe_float(summary.get("板块异动总次数")) or 0)
        leader = _env_text(summary.get("板块异动最频繁个股及所属类型-股票名称"))
        action = _env_text(summary.get("板块异动最频繁个股及所属类型-买卖方向"))
        parts = [f"{topic} 当前涨跌幅 {change_pct:.2f}%" if change_pct is not None else f"{topic} 当前有异动记录"]
        if event_count:
            parts.append(f"盘中异动 {event_count} 次")
        if leader:
            parts.append(f"高频异动个股为 {leader}{f'（{action}）' if action else ''}")
        return "，".join(parts) + "。"

    def _build_hotspot_route(self, topic: str, summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        """构造题材事件时间线：优先同花顺摘要，再叠加当日异动结构。"""
        route_by_date: Dict[str, Dict[str, Any]] = {}
        today = datetime.now().date().isoformat()

        def put_daily_item(*, date: str, title: str, description: str, source: str) -> None:
            """按日期写入时间线条目；同日已有条目时合并描述与来源。"""
            day = date or today
            existing = route_by_date.get(day)
            if existing:
                existing["description"] = f"{existing['description']}；{description}"
                if source and source not in str(existing.get("source") or ""):
                    existing["source"] = f"{existing.get('source')},{source}"
                return
            route_by_date[day] = {
                "title": title,
                "description": description,
                "source": source,
                "date": day,
                "published_at": day,
            }

        ths_event = self._fetch_ths_summary_event(topic)
        if ths_event:
            event_date = self._extract_route_date(ths_event) or today
            put_daily_item(
                date=event_date,
                title="题材驱动",
                description=ths_event,
                source="ths_summary",
            )
        if summary:
            change_events = self._parse_change_events(summary.get("板块具体异动类型列表及出现次数"))[:5]
            event_text = "；".join(f"{item['label']}出现 {item['count']} 次" for item in change_events)
            description = self._build_hotspot_summary(topic, summary)
            if event_text:
                description = f"{description} 当日结构：{event_text}。"
            put_daily_item(
                date=today,
                title="当日发酵",
                description=description,
                source="eastmoney_board_change",
            )
        route = [
            route_by_date[date]
            for date in sorted(route_by_date.keys(), reverse=True)
        ]
        if not route:
            route.append({
                "title": "等待发酵",
                "description": "暂未获取到明确催化事件，可继续观察涨跌幅、成交额和核心个股联动。",
                "source": "fallback",
                "date": today,
                "published_at": today,
            })
        return route

    def _extract_route_date(self, text: str) -> str:
        """从一段文本里抽出 ``YYYY-MM-DD`` 日期；找不到时返回空串。"""
        match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text or "")
        if not match:
            return ""
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"

    def _parse_change_events(self, raw: Any) -> List[Dict[str, Any]]:
        """把上游的异动事件列表（可能是字符串字面量）解析成结构化条目并按次数倒序。"""
        if isinstance(raw, str):
            try:
                import ast

                raw = ast.literal_eval(raw)
            except Exception:
                raw = []
        events = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            event_type = int(_safe_float(item.get("t")) or 0)
            count = int(_safe_float(item.get("ct")) or 0)
            if not count:
                continue
            events.append({
                "type": event_type,
                "label": self._CHANGE_EVENT_LABELS.get(event_type, f"异动类型 {event_type}"),
                "count": count,
            })
        return sorted(events, key=lambda item: item["count"], reverse=True)

    def _fetch_ths_summary_event(self, topic: str) -> str:
        """拉取同花顺的概念摘要事件（"驱动事件"），失败时返回空串。"""
        import akshare as ak
        from data_provider.akshare_fetcher import _akshare_call_with_timeout

        try:
            df = _akshare_call_with_timeout(
                ak.stock_board_concept_summary_ths,
                timeout=self._akshare_timeout_seconds(),
                call_name="screening.stock_board_concept_summary_ths",
            )
        except Exception:
            return ""
        if df is None or df.empty or "概念名称" not in df.columns:
            return ""
        rows = df[df["概念名称"].astype(str) == topic]
        if rows.empty:
            rows = df[df["概念名称"].astype(str).str.contains(re.escape(topic), case=False, na=False)]
        if rows.empty:
            return ""
        row = rows.iloc[0]
        date = _env_text(row.get("日期"))
        event = _env_text(row.get("驱动事件"))
        return f"{date}：{event}" if date and event else event

    def _fetch_ths_info(self, topic: str) -> Dict[str, str]:
        """拉取同花顺概念"项目-值"表，返回 ``{项目: 值}`` 字典。"""
        import akshare as ak
        from data_provider.akshare_fetcher import _akshare_call_with_timeout

        try:
            df = _akshare_call_with_timeout(
                ak.stock_board_concept_info_ths,
                symbol=topic,
                timeout=self._akshare_timeout_seconds(),
                call_name="screening.stock_board_concept_info_ths",
            )
        except Exception:
            return {}
        if df is None or df.empty or "项目" not in df.columns or "值" not in df.columns:
            return {}
        return {
            _env_text(row.get("项目")): _env_text(row.get("值"))
            for _, row in df.iterrows()
            if _env_text(row.get("项目"))
        }

    def _fetch_eastmoney_constituents(self, topic: str, *, source: str) -> Any:
        """通过 AkShare 拉取概念或行业的成份股原始 DataFrame。"""
        import akshare as ak
        from data_provider.akshare_fetcher import _akshare_call_with_timeout

        fetch = (
            ak.stock_board_industry_cons_em
            if source == "industry"
            else ak.stock_board_concept_cons_em
        )
        return _akshare_call_with_timeout(
            fetch,
            symbol=topic,
            timeout=self._akshare_timeout_seconds(),
            call_name=f"screening.{fetch.__name__}",
        )

    def _fetch_ths_constituents(self, topic: str) -> Any:
        """通过同花顺网页解析得到题材成份股（最多 80 条）。"""
        import pandas as pd
        import requests

        code = self._resolve_ths_concept_code(topic)
        if not code:
            return pd.DataFrame()
        response = requests.get(
            f"https://q.10jqka.com.cn/gn/detail/code/{code}/",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://q.10jqka.com.cn/gn/"},
            timeout=self._http_timeout(),
        )
        response.raise_for_status()
        html = response.content.decode("gbk", "ignore")
        rows = []
        seen = set()
        for match in re.finditer(r">(\d{6})<.*?>([^<>\n]{2,12})<", html, re.S):
            code_text = match.group(1)
            name_text = re.sub(r"\s+", "", match.group(2))
            if code_text in seen or not name_text or re.search(r"\d", name_text):
                continue
            seen.add(code_text)
            rows.append({"code": code_text, "name": name_text})
            if len(rows) >= 80:
                break
        return pd.DataFrame(rows)

    def _resolve_ths_concept_code(self, topic: str) -> str:
        """把题材名解析成同花顺的概念 code（精确 → 模糊 → 去尾"概念"再模糊）。"""
        df = self._fetch_ths_concept_names()
        if df is None or df.empty:
            return ""
        names = df["name"].map(_env_text)
        candidates = _dedupe_strings([topic, *self._THS_TOPIC_ALIASES.get(topic, ())])
        for candidate in candidates:
            rows = df[names == candidate]
            if not rows.empty:
                return _env_text(rows.iloc[0].get("code"))
        for candidate in candidates:
            rows = df[names.str.contains(re.escape(candidate), case=False, na=False)]
            if not rows.empty:
                return _env_text(rows.iloc[0].get("code"))
        if topic.endswith("概念"):
            rows = df[names.str.contains(re.escape(topic[:-2]), case=False, na=False)]
            if not rows.empty:
                return _env_text(rows.iloc[0].get("code"))
        return ""

    def _fetch_ths_concept_names(self) -> Any:
        """拉取同花顺的概念名-代码对照表，供题材名归一化使用。"""
        import akshare as ak
        from data_provider.akshare_fetcher import _akshare_call_with_timeout

        return _akshare_call_with_timeout(
            ak.stock_board_concept_name_ths,
            timeout=self._akshare_timeout_seconds(),
            call_name="screening.stock_board_concept_name_ths",
        )

    def _fallback_constituents(self, topic: str) -> Any:
        """用板块异动摘要里的"高频异动个股"作为成份股的兜底行。"""
        import pandas as pd

        try:
            summary = self._find_board_change(topic)
        except Exception as exc:
            logger.warning(
                "Screening board-change constituent fallback failed for %s; trying other sources: %s",
                topic,
                exc,
            )
            return pd.DataFrame()
        code = _env_text(summary.get("板块异动最频繁个股及所属类型-股票代码"))
        name = _env_text(summary.get("板块异动最频繁个股及所属类型-股票名称"))
        if not code and not name:
            return pd.DataFrame()
        return pd.DataFrame([{
            "code": code,
            "name": name,
            "change_pct": None,
            "hot_stock_score": 60.0,
            "fallback_used": True,
            "source": "board_change_leader_fallback",
        }])

    def _related_hotspot_constituents(self, topic: str) -> Any:
        """从同一金属分组下其他板块里聚合"活跃股"作为相关成份股。"""
        import pandas as pd

        group = self._hotspot_group(topic)
        if not group:
            return pd.DataFrame()
        try:
            raw = self._fetch_board_changes_raw()
        except Exception:
            return pd.DataFrame()
        df = pd.DataFrame(raw)
        if df.empty:
            return pd.DataFrame()
        rows: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for _, row in df.iterrows():
            board_name = _env_text(row.get("板块名称"))
            if not board_name or self._hotspot_group(board_name) != group:
                continue
            code = _env_text(row.get("板块异动最频繁个股及所属类型-股票代码"))
            name = _env_text(row.get("板块异动最频繁个股及所属类型-股票名称"))
            if not code and not name:
                continue
            key = code or name
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "code": code,
                "name": name,
                "change_pct": _safe_float(row.get("涨跌幅")),
                "role": f"{group}活跃股",
                "hot_stock_score": 35.0,
                "source": "eastmoney_board_change.related_group",
            })
            if len(rows) >= 12:
                break
        return pd.DataFrame(rows)

    def _get_constituent_cache(self, source: str, topic: str) -> Any:
        """读取已缓存的成份股 DataFrame；未命中返回 ``None``。"""
        import pandas as pd

        if not hasattr(self, "_constituent_cache"):
            self._constituent_cache = {}
        frame = self._constituent_cache.get((source, _env_text(topic)))
        if frame is None:
            return None
        return pd.DataFrame(frame).copy()

    def _set_constituent_cache(self, source: str, topic: str, frame: Any) -> None:
        """把成份股 DataFrame 写入本地缓存（使用 ``copy`` 防止外部修改）。"""
        import pandas as pd

        if not hasattr(self, "_constituent_cache"):
            self._constituent_cache = {}
        self._constituent_cache[(source, _env_text(topic))] = pd.DataFrame(frame).copy()

    def _merge_constituent_frames(self, frames: List[Any]) -> Any:
        """把多份成份股 DataFrame 合并成一份，按 code/name 去重。"""
        import pandas as pd

        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for frame in frames:
            df = pd.DataFrame(frame)
            if df.empty:
                continue
            for _, row in df.iterrows():
                code = _env_text(row.get("code") or row.get("代码") or row.get("证券代码"))
                name = _env_text(row.get("name") or row.get("名称") or row.get("股票名称"))
                if not code and not name:
                    continue
                key = code or name
                if key in seen:
                    continue
                seen.add(key)
                record = row.to_dict()
                record.setdefault("code", code)
                record.setdefault("name", name)
                merged.append(record)
        return pd.DataFrame(merged)

    def _normalize_constituent_records(self, frame: Any) -> List[Dict[str, Any]]:
        """把成份股 DataFrame 转成前端友好的字典列表（补齐涨跌/成交额等字段）。"""
        import pandas as pd

        df = pd.DataFrame(frame)
        if df.empty:
            return []
        records = []
        for _, row in df.iterrows():
            code = _env_text(row.get("code") or row.get("代码") or row.get("证券代码"))
            name = _env_text(row.get("name") or row.get("名称") or row.get("股票名称"))
            if not code and not name:
                continue
            records.append({
                "code": code,
                "name": name,
                "change_pct": _safe_float(row.get("change_pct") or row.get("涨跌幅") or row.get("涨幅")),
                "amount": _safe_float(row.get("amount") or row.get("成交额") or row.get("成交金额")),
                "turnover_rate": _safe_float(row.get("turnover_rate") or row.get("换手率")),
                "volume_ratio": _safe_float(row.get("volume_ratio") or row.get("量比")),
                "role": _env_text(row.get("role")) or "概念股",
                "hot_stock_score": _safe_float(row.get("hot_stock_score")) or 0.0,
            })
        return records


def _build_screening_context(config: Config, *, max_results: Optional[int] = None) -> Dict[str, Any]:
    """构造选股 pipeline 的 ``context`` 字典。

    ``context.llm.model`` / ``fallback`` / ``model_list`` 与 LiteLLM 路由语义保持一致，
    参考 https://docs.litellm.ai/docs/proxy/configs#the-model_list-key 。
    """
    channels = _normalize_dsa_llm_channels(config)
    litellm_model, fallback_models = _resolve_screening_llm_models(config)
    return {
        "llm": {
            "model": litellm_model,
            "fallback_models": fallback_models,
            "temperature": config.llm_temperature,
            "channels": channels,
            "model_list": _build_screening_litellm_model_list(config, channels),
            "litellm_config_path": config.litellm_config_path or "",
            "candidate_context_enabled": False,
            "candidate_multiplier": DSA_SCREENING_LLM_CANDIDATE_MULTIPLIER,
            "max_candidates": _resolve_dsa_llm_max_candidates(max_results),
        },
        "dsa": {
            "contract_version": "1",
            "mode": "pre_rank_light",
            "max_candidates": DSA_PRE_RANK_CONTEXT_MAX_CANDIDATES,
            "include_news": False,
            "news_max_results": 0,
            "capabilities": [
                "candidate_context",
                "daily_history",
                "realtime_quote",
                "fundamental_context",
                "stock_events",
            ],
            "get_candidate_context": get_dsa_candidate_context,
            "get_daily_history": get_dsa_daily_history,
            "get_realtime_quote": get_dsa_realtime_quote,
            "get_fundamental_context": get_dsa_fundamental_context,
        },
    }


@contextmanager
def _screening_litellm_headers(config: Config) -> Iterator[None]:
    """在上下文范围内把 DSA 渠道的 ``extra_headers`` 注入到 litellm 调用里。

    注入逻辑会在第一次进入时把 ``litellm.completion`` 替换为一个带 ``extra_headers``
    的包装函数；后续进入时直接复用上下文变量。
    """
    header_routes = _build_screening_litellm_header_routes(config)
    if not header_routes:
        yield
        return

    try:
        litellm_module = importlib.import_module("litellm")
    except Exception:
        yield
        return

    completion = getattr(litellm_module, "completion", None)
    if not callable(completion):
        yield
        return

    bridge_completion = getattr(completion, _SCREENING_LITELLM_COMPLETION_ATTR, None)
    if bridge_completion:
        token = _SCREENING_LITELLM_COMPLETION_ROUTES.set(
            tuple(route.copy() for route in header_routes),
        )
        try:
            yield
        finally:
            _SCREENING_LITELLM_COMPLETION_ROUTES.reset(token)
        return

    original_completion = completion

    def completion_with_dsa_headers(*args: Any, **kwargs: Any) -> Any:
        """包装 litellm.completion：按当前路由注入匹配渠道的请求头。"""
        routes = _SCREENING_LITELLM_COMPLETION_ROUTES.get()
        if routes:
            headers = _match_screening_litellm_headers(args, kwargs, routes)
            if headers:
                existing_headers = kwargs.get("extra_headers")
                if isinstance(existing_headers, dict):
                    merged_headers = dict(headers)
                    merged_headers.update(existing_headers)
                    kwargs = dict(kwargs)
                    kwargs["extra_headers"] = merged_headers
                elif existing_headers in (None, ""):
                    kwargs = dict(kwargs)
                    kwargs["extra_headers"] = dict(headers)
        return original_completion(*args, **kwargs)

    setattr(completion_with_dsa_headers, _SCREENING_LITELLM_COMPLETION_ATTR, True)
    setattr(completion_with_dsa_headers, "_screening_litellm_completion_original", original_completion)
    completion_with_dsa_headers.__name__ = "completion_with_dsa_headers"

    if completion is not completion_with_dsa_headers:
        with _SCREENING_LITELLM_COMPLETION_LOCK:
            if not getattr(getattr(litellm_module, "completion", None), _SCREENING_LITELLM_COMPLETION_ATTR, False):
                setattr(litellm_module, "completion", completion_with_dsa_headers)

    token = _SCREENING_LITELLM_COMPLETION_ROUTES.set(
        tuple(route.copy() for route in header_routes),
    )
    try:
        yield
    finally:
        _SCREENING_LITELLM_COMPLETION_ROUTES.reset(token)


def _build_screening_litellm_model_list(config: Config, channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """返回 LiteLLM 风格的 ``model_list``；优先使用用户显式配置，否则按渠道生成。"""
    explicit_model_list = _to_plain(config.llm_model_list or [])
    if isinstance(explicit_model_list, list) and explicit_model_list:
        return explicit_model_list
    return _channel_litellm_model_list(channels)


def _channel_litellm_model_list(channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按渠道 + 模型 + key 展开成 LiteLLM ``model_list`` 结构。"""
    model_list_builder = getattr(Config, "_channels_to_model_list", None)
    if callable(model_list_builder):
        return _to_plain(model_list_builder(channels))

    model_list: List[Dict[str, Any]] = []
    for channel in channels:
        headers = dict(channel.get("extra_headers") or {})
        base_url = _env_text(channel.get("base_url"))
        for model_name in channel.get("models") or []:
            for api_key in channel.get("api_keys") or []:
                litellm_params: Dict[str, Any] = {"model": model_name}
                if api_key:
                    litellm_params["api_key"] = api_key
                if base_url:
                    litellm_params["api_base"] = base_url
                if headers:
                    litellm_params["extra_headers"] = dict(headers)
                model_list.append({"model_name": model_name, "litellm_params": litellm_params})
    return model_list


def _build_screening_litellm_header_routes(config: Config) -> List[Dict[str, Any]]:
    """从 ``model_list`` 抽取每个模型对应的 ``extra_headers`` 路由表。"""
    channels = _normalize_dsa_llm_channels(config)
    model_list = _build_screening_litellm_model_list(config, channels)
    routes: List[Dict[str, Any]] = []
    for entry in model_list:
        if not isinstance(entry, dict):
            continue
        params = entry.get("litellm_params") or {}
        if not isinstance(params, dict):
            continue
        headers = params.get("extra_headers")
        if not isinstance(headers, dict) or not headers:
            continue
        model_names = _dedupe_strings([
            entry.get("model_name"),
            params.get("model"),
        ])
        if not model_names:
            continue
        routes.append(
            {
                "models": model_names,
                "api_key": _env_text(params.get("api_key")),
                "api_base": _env_text(params.get("api_base") or params.get("base_url")),
                "extra_headers": dict(headers),
            }
        )
    return routes


def _match_screening_litellm_headers(
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
    routes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """根据当前调用入参匹配一条 header 路由，未命中则返回空 dict。"""
    model = _env_text(kwargs.get("model"))
    if not model and args:
        model = _env_text(args[0])
    if not model:
        return {}

    api_key = _env_text(kwargs.get("api_key"))
    api_base = _env_text(kwargs.get("api_base") or kwargs.get("base_url"))
    for route in routes:
        if model not in set(route.get("models") or []):
            continue
        route_api_key = _env_text(route.get("api_key"))
        if route_api_key and api_key and route_api_key != api_key:
            continue
        route_api_base = _env_text(route.get("api_base"))
        if route_api_base and api_base and route_api_base != api_base:
            continue
        headers = route.get("extra_headers")
        return dict(headers) if isinstance(headers, dict) else {}
    return {}


def _resolve_dsa_llm_max_candidates(max_results: Optional[int]) -> int:
    """根据用户请求数量推导 LLM 候选数量（带放大系数与硬上限）。"""
    requested = max_results if isinstance(max_results, int) and max_results > 0 else DSA_ENRICHMENT_MAX_CANDIDATES
    return min(
        DSA_SCREENING_LLM_MAX_CANDIDATES,
        max(requested, requested * DSA_SCREENING_LLM_CANDIDATE_MULTIPLIER),
    )


def _resolve_screening_llm_models(config: Config) -> Tuple[str, List[str]]:
    """综合显式配置与渠道模型，决定主模型与 fallback 列表。"""
    primary = _env_text(config.litellm_model)
    configured_models = get_configured_llm_models(config.llm_model_list or [])
    configured_model_set = set(configured_models)

    if configured_models and (
        not primary or (primary not in configured_model_set and _is_managed_litellm_model(primary))
    ):
        primary = configured_models[0]

    raw_fallbacks = _dedupe_strings(config.litellm_fallback_models or [])
    if not configured_models:
        return primary, [model for model in raw_fallbacks if model != primary]

    fallback_models: List[str] = []
    seen = {primary} if primary else set()

    for model in raw_fallbacks:
        if model in seen:
            continue
        if model in configured_model_set or not _is_managed_litellm_model(model):
            fallback_models.append(model)
            seen.add(model)

    for model in configured_models:
        if model and model not in seen:
            fallback_models.append(model)
            seen.add(model)

    return primary, fallback_models


def _is_managed_litellm_model(model: str) -> bool:
    """判断模型是否落在 DSA 管理的 provider 集合内（用于决定走自动注入）。"""
    text = _env_text(model)
    if not text:
        return False
    provider = text.split("/", 1)[0].lower() if "/" in text else "openai"
    return provider in SCREENING_MANAGED_LITELLM_PROVIDERS


def _normalize_dsa_llm_channels(config: Config) -> List[Dict[str, Any]]:
    """把 ``config.llm_channels`` 列表规整成下游可直接消费的字典结构。"""
    channels: List[Dict[str, Any]] = []
    for index, raw in enumerate(config.llm_channels or []):
        if not isinstance(raw, dict):
            continue
        name = _env_text(raw.get("name")) or f"channel{index + 1}"
        api_keys = _dedupe_strings(raw.get("api_keys") if isinstance(raw.get("api_keys"), list) else [])
        models = _dedupe_strings(raw.get("models") if isinstance(raw.get("models"), list) else [])
        channel = {
            "name": name,
            "protocol": _env_text(raw.get("protocol")),
            "api_surface": normalize_llm_channel_api_surface(raw.get("api_surface")),
            "base_url": _env_text(raw.get("base_url")),
            "api_keys": api_keys,
            "models": models,
            "extra_headers": raw.get("extra_headers") if isinstance(raw.get("extra_headers"), dict) else {},
            "enabled": bool(raw.get("enabled", True)),
        }
        if channel["enabled"] and (api_keys or models or channel["base_url"] or channel["extra_headers"]):
            channels.append(channel)
    return channels


def _channel_keys_for_provider(channels: List[Dict[str, Any]], providers: set[str]) -> List[str]:
    """收集属于指定 provider 集合的渠道 API key。"""
    keys: List[str] = []
    for channel in channels:
        protocol = _env_text(channel.get("protocol")).lower()
        models = channel.get("models") or []
        model_providers = {
            str(model).split("/", 1)[0].lower()
            for model in models
            if isinstance(model, str) and "/" in model
        }
        if protocol in providers or model_providers.intersection(providers):
            keys.extend(channel.get("api_keys") or [])
    return keys


def _first_channel_base_url(channels: List[Dict[str, Any]], providers: set[str]) -> str:
    """返回首个匹配 provider 的渠道 base_url，没有匹配时返回空串。"""
    for channel in channels:
        protocol = _env_text(channel.get("protocol")).lower()
        base_url = _env_text(channel.get("base_url"))
        if base_url and protocol in providers:
            return base_url
    return ""


def _put_provider_keys(env: Dict[str, str], provider: str, keys: List[str]) -> None:
    """把 ``keys`` 写入 ``env`` 的 ``PROVIDER_API_KEYS`` 与 ``PROVIDER_API_KEY``。"""
    if not keys:
        return
    env[f"{provider}_API_KEYS"] = ",".join(keys)
    env[f"{provider}_API_KEY"] = keys[0]


def _dedupe_strings(values: Any) -> List[str]:
    """把任意输入去重成"只含非空字符串"的列表，保留首次出现顺序。"""
    result: List[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return result
    for value in values:
        text = _env_text(value)
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _collect_screening_warning_messages(payload: Dict[str, Any]) -> List[str]:
    """把 ``warnings`` / ``degradation`` 字段合并、去重，输出统一的告警列表。"""
    warnings: List[str] = []
    seen: set[str] = set()
    for key in ("warnings", "degradation"):
        for value in _list_text_values(payload.get(key)):
            if value in seen:
                continue
            seen.add(value)
            warnings.append(value)
    return warnings


def _env_text(value: Any) -> str:
    """把任意值规范成"非空字符串"，过滤掉 ``nan`` / ``none`` / ``null`` 等伪值。"""
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _get_dsa_fetcher_manager() -> Any:
    """懒加载并缓存 DSA 的 ``DataFetcherManager`` 实例（线程安全）。"""
    global _DSA_FETCHER_MANAGER
    if _DSA_FETCHER_MANAGER is None:
        with _DSA_FETCHER_MANAGER_LOCK:
            if _DSA_FETCHER_MANAGER is None:
                from data_provider import DataFetcherManager

                _DSA_FETCHER_MANAGER = DataFetcherManager()
    return _DSA_FETCHER_MANAGER


def _fetch_dsa_hotspot_rankings(source: str, limit: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """在调用方的可终止子进程内拉取 DSA 的概念/行业排行（top, bottom）。"""
    manager = _get_dsa_fetcher_manager()
    fetch = manager.get_concept_rankings if source == "concept" else manager.get_sector_rankings
    return fetch(limit)


def _get_dsa_search_service() -> Any:
    """返回全局 DSA 搜索服务单例。"""
    from src.search_service import get_search_service

    return get_search_service()


def get_dsa_daily_history(stock_code: str, *, lookback_days: int = 120) -> Tuple[Any, str]:
    """从 DSA 历史加载器读取日线 DataFrame 与实际数据源名。"""
    from src.services.history_loader import load_history_df

    normalized_code = _env_text(stock_code).zfill(6)
    days = max(int(lookback_days or 0), 30)
    return load_history_df(normalized_code, days=days)


def _normalize_dsa_daily_history(raw_df: Any) -> Any:
    """把 DSA 日线 DataFrame 规整为统一列名（date/open/high/low/close/volume/amount）。"""
    if raw_df is None:
        return None

    import pandas as pd

    df = pd.DataFrame(raw_df).copy()
    if df.empty:
        return df

    aliases = {
        "date": ("date", "trade_date", "datetime", "日期"),
        "open": ("open", "开盘"),
        "high": ("high", "最高"),
        "low": ("low", "最低"),
        "close": ("close", "收盘", "price"),
        "volume": ("volume", "vol", "成交量"),
        "amount": ("amount", "成交额"),
    }
    normalized = pd.DataFrame(index=df.index)
    for target, candidates in aliases.items():
        source_column = next((column for column in candidates if column in df.columns), None)
        if source_column is not None:
            normalized[target] = df[source_column]

    if "close" not in normalized.columns:
        return pd.DataFrame()
    for column in ("open", "high", "low"):
        if column not in normalized.columns:
            normalized[column] = normalized["close"]
    if "volume" not in normalized.columns:
        normalized["volume"] = 0

    if "date" in normalized.columns:
        normalized["date"] = normalized["date"].map(_normalize_daily_date_value)

    for column in ("open", "high", "low", "close", "volume", "amount"):
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=["close"])
    return normalized.reset_index(drop=True)


def _normalize_daily_date_value(value: Any) -> str:
    """把 ``YYYYMMDD`` 形式的日期转成 ``YYYY-MM-DD``；其它情况原样返回。"""
    text = _env_text(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def get_dsa_realtime_quote(stock_code: str) -> Dict[str, Any]:
    """返回单只股票的实时行情字典（NaN/Inf 会被清理）。"""
    manager = _get_dsa_fetcher_manager()
    quote = manager.get_realtime_quote(stock_code, log_final_failure=False)
    if quote is None:
        return {}
    if hasattr(quote, "to_dict") and callable(quote.to_dict):
        return _remove_non_finite_json_values(quote.to_dict())
    payload = _to_plain(quote)
    return _remove_non_finite_json_values(payload if isinstance(payload, dict) else {})


def get_dsa_fundamental_context(stock_code: str) -> Dict[str, Any]:
    """返回精简后的基本面上下文（只保留 status/data 等关键字段）。"""
    manager = _get_dsa_fetcher_manager()
    context = manager.get_fundamental_context(stock_code, budget_seconds=4.0)
    return _compact_fundamental_context(_remove_non_finite_json_values(_to_plain(context)))


def search_dsa_stock_news(stock_code: str, stock_name: str = "", max_results: int = 3) -> Dict[str, Any]:
    """通过 DSA 搜索服务拉取个股新闻，归一化后返回给选股使用。"""
    service = _get_dsa_search_service()
    if not getattr(service, "is_available", False):
        return {
            "success": False,
            "error": "DSA search service unavailable",
            "results": [],
        }

    response = service.search_stock_news(stock_code, stock_name or stock_code, max_results=max_results)
    return _normalize_dsa_search_response(response, max_results=max_results)


def search_dsa_stock_events(stock_code: str, stock_name: str = "", max_results: int = 3) -> Dict[str, Any]:
    """复用 DSA 事件搜索，覆盖业绩/减持/公告等事件上下文。"""
    service = _get_dsa_search_service()
    if not getattr(service, "is_available", False):
        return {
            "success": False,
            "error": "DSA search service unavailable",
            "results": [],
        }

    response = service.search_stock_events(stock_code, stock_name or stock_code)
    return _normalize_dsa_search_response(response, max_results=max_results)


def _normalize_dsa_search_response(response: Any, *, max_results: int) -> Dict[str, Any]:
    """把搜索服务的响应对象转成可序列化的字典结构。"""
    results = []
    for item in (getattr(response, "results", []) or [])[:max(0, int(max_results))]:
        results.append(
            {
                "title": getattr(item, "title", ""),
                "snippet": getattr(item, "snippet", ""),
                "url": getattr(item, "url", ""),
                "source": getattr(item, "source", ""),
                "published_date": getattr(item, "published_date", None),
            }
        )
    return _remove_non_finite_json_values(
        {
            "query": getattr(response, "query", ""),
            "provider": getattr(response, "provider", ""),
            "success": bool(getattr(response, "success", False)),
            "error": getattr(response, "error_message", None),
            "results": results,
        }
    )


def get_dsa_candidate_context(
    stock_code: str,
    stock_name: str = "",
    *,
    include_news: bool = False,
    include_fundamentals: bool = True,
    mode: str = "pre_rank_light",
) -> Dict[str, Any]:
    """预排序阶段使用的轻量级候选上下文入口。"""
    candidate = {"code": stock_code, "name": stock_name, "raw": {}}
    context = _build_dsa_candidate_context(
        candidate,
        include_news=include_news,
        include_events=include_news,
        include_fundamentals=include_fundamentals,
        profile=mode or "pre_rank_light",
    )
    return context.get("dsa_context", {})


def _enrich_candidates_with_dsa(candidates: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """对最终入选候选执行 DSA 后置增广（行情、新闻、事件、基本面）。"""
    enriched_count = 0
    warnings: List[str] = []
    limit = min(len(candidates), DSA_ENRICHMENT_MAX_CANDIDATES)

    for index, candidate in enumerate(candidates):
        if index >= limit:
            continue
        existing_context = candidate.get("dsa_context")
        if (
            isinstance(existing_context, dict)
            and existing_context.get("enriched")
            and _candidate_has_dsa_news(candidate)
        ):
            enriched_count += 1
            existing_warnings = existing_context.get("warnings") or []
            if isinstance(existing_warnings, list):
                warnings.extend(str(item) for item in existing_warnings if item)
            elif existing_warnings:
                warnings.append(str(existing_warnings))
            continue
        try:
            enriched = _build_dsa_candidate_context(
                candidate,
                include_news=True,
                include_fundamentals=True,
                profile="post_rank_full",
            )
            candidate.update(enriched)
            if enriched.get("dsa_context", {}).get("enriched"):
                enriched_count += 1
            warnings.extend(enriched.get("dsa_context", {}).get("warnings") or [])
        except Exception as exc:  # noqa: BLE001 - DSA enrichment must not block screening.
            code = candidate.get("code") or f"rank-{candidate.get('rank', index + 1)}"
            message = f"{code}: {exc}"
            warnings.append(message)
            logger.warning("DSA enrichment failed for Screening candidate %s: %s", code, exc)
            candidate["dsa_context"] = {
                "enriched": False,
                "warnings": [message],
            }

    return candidates, {
        "enabled": True,
        "max_candidates": DSA_ENRICHMENT_MAX_CANDIDATES,
        "requested_count": limit,
        "enriched_count": enriched_count,
        "warnings": _dedupe_strings(warnings),
    }


def _candidate_has_dsa_news(candidate: Dict[str, Any]) -> bool:
    """判断一个候选是否已经含有 DSA 新闻数据（用于跳过重复增广）。"""
    news_items = candidate.get("dsa_news")
    if isinstance(news_items, list) and any(isinstance(item, dict) for item in news_items):
        return True
    context = candidate.get("dsa_context")
    if not isinstance(context, dict):
        return False
    return _news_has_results(context.get("news"))


def _news_has_results(news: Any) -> bool:
    """判断一个 ``news`` 字段是否带有真实的搜索结果。"""
    if isinstance(news, dict):
        results = news.get("results")
        return isinstance(results, list) and any(isinstance(item, dict) for item in results)
    if isinstance(news, list):
        return any(isinstance(item, dict) for item in news)
    return False


def _build_dsa_candidate_context(
    candidate: Dict[str, Any],
    *,
    include_news: bool = True,
    include_events: bool = True,
    include_fundamentals: bool = True,
    profile: str = "post_rank_full",
) -> Dict[str, Any]:
    """按 profile（预排序/后置）拉取行情、新闻、事件、基本面等上下文。"""
    code = _env_text(candidate.get("code"))
    name = _env_text(candidate.get("name"))
    warnings: List[str] = []
    if not code:
        return {
            "dsa_context": {
                "enriched": False,
                "warnings": ["missing candidate code"],
            }
        }

    existing_context = candidate.get("dsa_context")
    if not isinstance(existing_context, dict):
        existing_context = {}

    quote = existing_context.get("quote") if isinstance(existing_context.get("quote"), dict) else {}
    fundamentals = (
        existing_context.get("fundamentals")
        if isinstance(existing_context.get("fundamentals"), dict)
        else {}
    )
    existing_news = existing_context.get("news") if isinstance(existing_context.get("news"), dict) else {}
    news: Dict[str, Any] = dict(existing_news) if existing_news else {"success": False, "results": []}
    existing_events = existing_context.get("events") if isinstance(existing_context.get("events"), dict) else {}
    events: Dict[str, Any] = dict(existing_events) if existing_events else {"success": False, "results": []}
    existing_warnings = existing_context.get("warnings") or []
    if isinstance(existing_warnings, list):
        warnings.extend(str(item) for item in existing_warnings if item)
    elif existing_warnings:
        warnings.append(str(existing_warnings))

    try:
        manager = _get_dsa_fetcher_manager()
        resolved_name = manager.get_stock_name(code, allow_realtime=False)
        if resolved_name and (not name or name == code):
            name = resolved_name
            candidate["name"] = resolved_name
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"stock_name_failed: {exc}")

    if not quote:
        try:
            quote = get_dsa_realtime_quote(code)
            if not quote:
                warnings.append("realtime_quote_missing")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"realtime_quote_failed: {exc}")
            quote = {}

    if quote:
        candidate["price"] = _first_non_empty(candidate.get("price"), quote.get("price"))
        candidate["change_pct"] = _first_non_empty(candidate.get("change_pct"), quote.get("change_pct"))
        candidate["amount"] = _first_non_empty(candidate.get("amount"), quote.get("amount"))
        if not candidate.get("name") and quote.get("name"):
            candidate["name"] = quote.get("name")

    if include_fundamentals and not fundamentals:
        try:
            fundamentals = get_dsa_fundamental_context(code)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"fundamental_context_failed: {exc}")
            fundamentals = {}

    if include_news:
        if not _news_has_results(news):
            try:
                news = search_dsa_stock_news(code, _env_text(candidate.get("name")) or name or code, max_results=3)
                if not news.get("success"):
                    warnings.append(news.get("error") or "stock_news_unavailable")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"stock_news_failed: {exc}")
                news = {"success": False, "error": str(exc), "results": []}
    elif not _news_has_results(news):
        news = {
            "success": False,
            "skipped": True,
            "reason": "pre_rank_light_context",
            "results": [],
        }

    if include_events:
        if not _news_has_results(events):
            try:
                events = search_dsa_stock_events(
                    code,
                    _env_text(candidate.get("name")) or name or code,
                    max_results=3,
                )
                if not events.get("success"):
                    warnings.append(events.get("error") or "stock_events_unavailable")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"stock_events_failed: {exc}")
                events = {"success": False, "error": str(exc), "results": []}
    elif not _news_has_results(events):
        events = {
            "success": False,
            "skipped": True,
            "reason": "pre_rank_light_context",
            "results": [],
        }

    summary = _build_dsa_analysis_summary(candidate, quote, fundamentals, news, events)
    context = {
        "enriched": bool(quote or fundamentals or news.get("results") or events.get("results")),
        "profile": profile,
        "news_included": bool(include_news),
        "events_included": bool(include_events),
        "quote": quote,
        "fundamentals": fundamentals,
        "news": news,
        "events": events,
        "warnings": _dedupe_strings(warnings),
    }
    return {
        "dsa_context": context,
        "dsa_news": news.get("results") or [],
        "dsa_events": events.get("results") or [],
        "dsa_analysis_summary": summary,
    }


def _first_non_empty(*values: Any) -> Any:
    """返回第一个非空（不为 ``None``/``""``）的值；全空时返回 ``None``。"""
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _compact_fundamental_context(context: Any) -> Dict[str, Any]:
    """精简基本面上下文，只保留 ``market`` / ``status`` / ``coverage`` 等关键字段。"""
    if not isinstance(context, dict):
        return {}
    compact: Dict[str, Any] = {
        "market": context.get("market"),
        "status": context.get("status"),
        "coverage": context.get("coverage") if isinstance(context.get("coverage"), dict) else {},
    }
    for block in _FUNDAMENTAL_BLOCKS:
        payload = context.get(block)
        if isinstance(payload, dict):
            compact[block] = {
                "status": payload.get("status"),
                "data": payload.get("data") if isinstance(payload.get("data"), dict) else {},
            }
    errors = context.get("errors")
    if isinstance(errors, list) and errors:
        compact["errors"] = [str(item) for item in errors[:3]]
    return compact


def _build_dsa_analysis_summary(
    candidate: Dict[str, Any],
    quote: Dict[str, Any],
    fundamentals: Dict[str, Any],
    news: Dict[str, Any],
    events: Optional[Dict[str, Any]] = None,
) -> str:
    """把行情/基本面/新闻/事件拼成一段给前端展示的"分析摘要"文本。"""
    parts: List[str] = []
    price = _first_non_empty(quote.get("price"), candidate.get("price"))
    change_pct = _first_non_empty(quote.get("change_pct"), candidate.get("change_pct"))
    if price is not None:
        text = f"DSA行情：现价 {price}"
        if change_pct is not None:
            text += f"，涨跌幅 {change_pct}%"
        parts.append(text)

    coverage = fundamentals.get("coverage") if isinstance(fundamentals, dict) else {}
    if isinstance(coverage, dict) and coverage:
        available_blocks = [key for key, value in coverage.items() if str(value).lower() in {"available", "partial"}]
        if available_blocks:
            parts.append(f"DSA基本面覆盖：{', '.join(available_blocks[:4])}")

    news_results = news.get("results") if isinstance(news, dict) else []
    if isinstance(news_results, list) and news_results:
        titles = [str(item.get("title") or "").strip() for item in news_results if isinstance(item, dict)]
        titles = [title for title in titles if title]
        if titles:
            parts.append(f"DSA新闻：{'；'.join(titles[:2])}")

    event_results = events.get("results") if isinstance(events, dict) else []
    if isinstance(event_results, list) and event_results:
        titles = [str(item.get("title") or "").strip() for item in event_results if isinstance(item, dict)]
        titles = [title for title in titles if title]
        if titles:
            parts.append(f"DSA事件：{'；'.join(titles[:2])}")

    if not parts:
        return ""
    return "；".join(parts)


def _ensure_supported_market(market: str) -> None:
    """校验 ``market`` 是否在引擎支持的市场范围内，否则抛出 422。"""
    status = _call_screening_status()
    supported_markets = status.get("supported_markets") or status.get("markets") or status.get("market")
    if not supported_markets:
        return

    normalized: List[Any]
    if isinstance(supported_markets, str):
        normalized = [supported_markets]
    elif isinstance(supported_markets, (list, tuple, set)):
        normalized = list(supported_markets)
    else:
        normalized = []

    if market not in normalized:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "screening_invalid_market",
                "message": (
                    f"市场 {market} 不在选股功能支持范围内"
                    f"（支持市场：{', '.join(map(str, normalized)) or '未知'}）。"
                ),
            },
        )


def _normalize_candidates(raw: Any) -> List[Dict[str, Any]]:
    """把上游 pipeline 返回的 candidates 列表统一规整为标准字段结构。"""
    data = _to_plain(raw)
    items = data
    if isinstance(data, dict):
        for key in ("candidates", "picks", "items", "results", "stocks"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    if not isinstance(items, list):
        return []
    return [_normalize_candidate(item, index + 1) for index, item in enumerate(items)]


def _normalize_candidate(raw: Any, rank: int) -> Dict[str, Any]:
    """把单条候选数据（兼容 ``item.raw`` 嵌套）转成对外的标准化结构。"""
    item = _remove_non_finite_json_values(_to_plain(raw))
    if not isinstance(item, dict):
        item = {"code": str(item)}
    source = item.get("raw") if isinstance(item.get("raw"), dict) else item
    dsa_context = item.get("dsa_context") or source.get("dsa_context") or {}
    dsa_news = item.get("dsa_news") or source.get("dsa_news") or _extract_dsa_news_from_context(dsa_context)
    dsa_events = item.get("dsa_events") or source.get("dsa_events") or _extract_dsa_events_from_context(dsa_context)
    dsa_analysis_summary = (
        item.get("dsa_analysis_summary")
        or source.get("dsa_analysis_summary")
        or _extract_dsa_analysis_summary_from_context(dsa_context)
    )
    return {
        "rank": item.get("rank") or source.get("rank") or rank,
        "code": item.get("code") or source.get("code") or item.get("symbol") or source.get("symbol") or item.get("stock_code") or source.get("stock_code") or "",
        "name": item.get("name") or source.get("name") or item.get("stock_name") or source.get("stock_name") or "",
        "score": _first_present(item, source, "score", "final_score"),
        "screen_score": _first_present(item, source, "screen_score"),
        "reason": item.get("reason") or source.get("reason") or source.get("ranking_reason") or source.get("risk_summary") or item.get("summary") or _build_candidate_reason(source),
        "risk_level": item.get("risk_level") or source.get("risk_level") or "",
        "risk_flags": item.get("risk_flags") or source.get("risk_flags") or [],
        "llm_score": _first_present(item, source, "llm_score"),
        "llm_confidence": _first_present(item, source, "llm_confidence"),
        "llm_sector": item.get("llm_sector") or source.get("llm_sector") or "",
        "llm_theme": item.get("llm_theme") or source.get("llm_theme") or "",
        "llm_tags": item.get("llm_tags") or source.get("llm_tags") or [],
        "llm_thesis": item.get("llm_thesis") or source.get("llm_thesis") or "",
        "llm_catalysts": item.get("llm_catalysts") or source.get("llm_catalysts") or [],
        "llm_risks": item.get("llm_risks") or source.get("llm_risks") or [],
        "llm_watch_items": item.get("llm_watch_items") or source.get("llm_watch_items") or [],
        "llm_invalidators": item.get("llm_invalidators") or source.get("llm_invalidators") or [],
        "llm_style_fit": item.get("llm_style_fit") or source.get("llm_style_fit") or "",
        "price": _first_present(item, source, "price"),
        "change_pct": _first_present(item, source, "change_pct"),
        "amount": _first_present(item, source, "amount"),
        "industry": item.get("industry") or source.get("industry") or "",
        "factor_scores": item.get("factor_scores") or source.get("factor_scores") or {},
        "dsa_context": dsa_context,
        "dsa_news": dsa_news,
        "dsa_events": dsa_events,
        "dsa_analysis_summary": dsa_analysis_summary,
        "post_analysis_summaries": item.get("post_analysis_summaries") or source.get("post_analysis_summaries") or {},
        "post_analysis_tags": item.get("post_analysis_tags") or source.get("post_analysis_tags") or [],
        "raw": source,
    }


def _extract_dsa_news_from_context(context: Any) -> List[Dict[str, Any]]:
    """从 ``dsa_context`` 中抽取规范化的新闻列表。"""
    if not isinstance(context, dict):
        return []
    news = context.get("news")
    if isinstance(news, dict):
        results = news.get("results")
    elif isinstance(news, list):
        results = news
    else:
        results = None
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _extract_dsa_events_from_context(context: Any) -> List[Dict[str, Any]]:
    """从 ``dsa_context`` 中抽取规范化的事件列表。"""
    if not isinstance(context, dict):
        return []
    events = context.get("events")
    if isinstance(events, dict):
        results = events.get("results")
    elif isinstance(events, list):
        results = events
    else:
        results = None
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _extract_dsa_analysis_summary_from_context(context: Any) -> str:
    """从 ``dsa_context`` 里按优先级提取"分析摘要"字符串。"""
    if not isinstance(context, dict):
        return ""
    for key in ("dsa_analysis_summary", "analysis_summary", "summary"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value
    news = context.get("news")
    if isinstance(news, dict):
        for key in ("analysis_summary", "summary"):
            value = news.get(key)
            if isinstance(value, str) and value.strip():
                return value
    news_items = _extract_dsa_news_from_context(context)
    if not news_items:
        return ""
    quote = context.get("quote") if isinstance(context.get("quote"), dict) else {}
    fundamentals = context.get("fundamentals") if isinstance(context.get("fundamentals"), dict) else {}
    return _build_dsa_analysis_summary({}, quote, fundamentals, {"results": news_items})


def _first_present(primary: Dict[str, Any], source: Dict[str, Any], *keys: str) -> Any:
    """按 ``keys`` 顺序依次从 ``primary`` 与 ``source`` 中取首个非空值。"""
    for key in keys:
        if primary.get(key) is not None:
            return primary.get(key)
        if source.get(key) is not None:
            return source.get(key)
    return None


def _build_candidate_reason(item: Dict[str, Any]) -> str:
    """基于 ``post_analysis_summaries`` / ``factor_scores`` 拼一段候选入选理由。"""
    summaries = item.get("post_analysis_summaries")
    if isinstance(summaries, dict):
        summary = next((str(value) for value in summaries.values() if value), "")
        if summary:
            return summary

    factors = item.get("factor_scores")
    parts: List[str] = []
    if isinstance(factors, dict) and factors:
        top_factors = sorted(
            ((key, value) for key, value in factors.items() if isinstance(value, (int, float))),
            key=lambda pair: pair[1],
            reverse=True,
        )[:3]
        if top_factors:
            factor_text = "、".join(f"{key} {value:.1f}" for key, value in top_factors)
            parts.append(f"主要因子：{factor_text}")
    if item.get("industry"):
        parts.append(f"行业：{item['industry']}")
    if item.get("risk_level"):
        parts.append(f"风险等级：{item['risk_level']}")
    return "；".join(parts)


def _to_plain(value: Any) -> Any:
    """把 dataclass / Pydantic / 自定义对象尽量转成纯 Python dict/list/基础类型。"""
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict") and callable(value.dict):
        return value.dict()
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    return value


def _remove_non_finite_json_values(value: Any) -> Any:
    """递归把 ``NaN`` / ``Inf`` 转成 ``None``，保证返回结果可被 JSON 序列化。"""
    if isinstance(value, list):
        return [_remove_non_finite_json_values(item) for item in value]
    if isinstance(value, tuple):
        return [_remove_non_finite_json_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _remove_non_finite_json_values(item) for key, item in value.items()}
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value
