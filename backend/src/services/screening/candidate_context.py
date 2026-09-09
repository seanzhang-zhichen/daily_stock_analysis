# -*- coding: utf-8 -*-
# Derived from AlphaSift revision 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf.
# Licensed under Apache-2.0 and modified for daily_stock_analysis.
"""可选的 Top-K 候选股上下文增强：新闻、公告与资金流向。

为候选池补充舆情/公告/资金流等外部上下文，供 LLM 排序阶段使用。
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# 负面事件关键词 -> 分类标签, 用于给 LLM 提示"是否要规避该候选"
_NEGATIVE_EVENT_KEYWORDS = {
    "减持": ("减持", "拟减持", "被动减持"),
    "监管": ("处罚", "立案", "监管函", "问询函", "警示函", "调查"),
    "业绩压力": ("预亏", "亏损", "业绩下滑", "业绩减少", "净利润下降"),
    "财务风险": ("债务", "逾期", "违约", "商誉减值", "资产减值"),
    "退市风险": ("退市", "ST", "*ST", "终止上市"),
    "诉讼风险": ("诉讼", "仲裁", "冻结", "质押"),
}
# 正面事件关键词 -> 分类标签
_POSITIVE_EVENT_KEYWORDS = {
    "回购增持": ("回购", "增持"),
    "订单经营": ("中标", "合同", "订单", "定点", "合作"),
    "业绩改善": ("预增", "扭亏", "增长", "净利润增长"),
    "股东回报": ("分红", "派息"),
    "激励": ("股权激励", "员工持股"),
}
# 公告类别关键词: 给公告文本做粗粒度分类, 提示"是否与基本面相关"
_ANNOUNCEMENT_CATEGORY_KEYWORDS = {
    "业绩": ("业绩", "利润", "营收", "预增", "预亏", "扭亏", "年报", "季报"),
    "回购增持": ("回购", "增持"),
    "减持": ("减持", "被动减持"),
    "监管问询": ("监管函", "问询函", "警示函", "立案", "调查", "处罚"),
    "重大合同": ("中标", "合同", "订单", "定点", "合作协议"),
    "分红融资": ("分红", "派息", "配股", "定增", "可转债", "融资"),
    "诉讼担保": ("诉讼", "仲裁", "担保", "冻结", "质押"),
    "股权激励": ("股权激励", "员工持股"),
}
# 各来源默认权重, 越高越可信(供来源覆盖率打分使用)
_SOURCE_WEIGHTS = {
    "announcement": 1.0,
    "quote": 0.85,
    "news": 0.65,
    "fund_flow": 0.75,
}
# Top-K 候选内最大并发抓取数; 任务数较少时不强制触发多线程
_DEFAULT_MAX_WORKERS = 4


def collect_candidate_context(
    candidate_df: pd.DataFrame,
    *,
    max_rows: int = 8,
    providers: list[str] | None = None,
    news_limit: int = 3,
    announcement_limit: int = 3,
    cache_dir: str | Path | None = None,
    cache_ttl_hours: int = 24,
    source_weights: dict[str, float] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    """按股票代码并发抓取 Top-K 候选的上下文素材, 返回 ``(rows, errors)``。

    注意: 该函数**不会影响候选资格**, 只是为已经入围的候选补充 LLM 研究素材。
    任意来源失败都不会中断整体流程, 只会累加到 ``errors`` 列表。
    """
    if candidate_df.empty or "code" not in candidate_df.columns or max_rows <= 0:
        return [], []

    providers = _normalize_providers(providers or [])
    tasks: list[dict[str, str]] = []
    # 仅取前 max_rows 行, 减少单轮 LLM 上下文的体积
    for _, candidate in candidate_df.head(max_rows).iterrows():
        code = _normalize_code(candidate.get("code", ""))
        # 000000 是常见的"占位/异常"代码, 跳过避免无效请求
        if not code or code == "000000":
            continue
        tasks.append(
            {
                "code": code,
                "name": str(candidate.get("name", "") or ""),
            }
        )

    if not tasks:
        return [], []

    results: list[tuple[dict[str, object] | None, list[str]] | None] = [None] * len(tasks)
    # 单任务时不启用线程池, 避免不必要的线程开销
    max_workers = min(_DEFAULT_MAX_WORKERS, len(tasks))
    if max_workers <= 1:
        for index, task in enumerate(tasks):
            results[index] = _collect_candidate_context_row(
                task,
                providers=providers,
                news_limit=news_limit,
                announcement_limit=announcement_limit,
                cache_dir=cache_dir,
                cache_ttl_hours=cache_ttl_hours,
                source_weights=source_weights,
            )
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(
                    _collect_candidate_context_row,
                    task,
                    providers=providers,
                    news_limit=news_limit,
                    announcement_limit=announcement_limit,
                    cache_dir=cache_dir,
                    cache_ttl_hours=cache_ttl_hours,
                    source_weights=source_weights,
                ): index
                for index, task in enumerate(tasks)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    # 抓取异常被降到单条 errors, 不影响其它候选
                    results[index] = (None, [f"{tasks[index]['code']} context: {exc}"])

    rows: list[dict[str, object]] = []
    errors: list[str] = []
    for result in results:
        if result is None:
            continue
        row, row_errors = result
        errors.extend(row_errors)
        if row is not None:
            rows.append(row)
    return rows, errors


def _collect_candidate_context_row(
    candidate: dict[str, str],
    *,
    providers: list[str],
    news_limit: int,
    announcement_limit: int,
    cache_dir: str | Path | None,
    cache_ttl_hours: int,
    source_weights: dict[str, float] | None,
) -> tuple[dict[str, object] | None, list[str]]:
    """对单只股票执行"读缓存 -> 抓四类来源 -> 写回缓存"流程。"""
    code = candidate["code"]
    errors: list[str] = []
    try:
        cached = _read_cache(cache_dir, code, providers, cache_ttl_hours=cache_ttl_hours)
        if cached is not None:
            # 命中缓存也要保证拥有最新计算字段(权重分/事件标签等)
            _ensure_context_row_enrichment(
                cached,
                requested_sources=providers,
                source_weights=source_weights,
            )
            return cached, []
        # 新建空 row, 由各来源分别填字段
        row: dict[str, object] = {
            "code": code,
            "name": candidate.get("name", ""),
        }
        successful_sources: list[str] = []
        if "news" in providers:
            try:
                row["news"] = fetch_stock_news_summary(code, limit=news_limit)
                if row["news"]:
                    successful_sources.append("news")
            except Exception as exc:
                errors.append(f"{code} news: {exc}")
        if "announcement" in providers or "announcements" in providers:
            # 兼容历史/现行两种命名
            try:
                row["announcement"] = fetch_stock_announcement_summary(code, limit=announcement_limit)
                if row["announcement"]:
                    successful_sources.append("announcement")
            except Exception as exc:
                errors.append(f"{code} announcement: {exc}")
        if "fund_flow" in providers or "fundflow" in providers:
            try:
                row["fund_flow"] = fetch_stock_fund_flow_summary(code)
                if row["fund_flow"]:
                    successful_sources.append("fund_flow")
            except Exception as exc:
                errors.append(f"{code} fund_flow: {exc}")
        if "quote" in providers:
            try:
                row["quote"] = fetch_stock_quote_summary(code)
                if row["quote"]:
                    successful_sources.append("quote")
            except Exception as exc:
                errors.append(f"{code} quote: {exc}")
        # 至少要有一种来源拉到了非空字段, 否则视为空 row 丢弃
        if any(value for key, value in row.items() if key not in {"code", "name"}):
            row["source_count"] = len(successful_sources)
            row["source_confidence"] = _source_confidence(successful_sources, providers)
            row["source_weight_score"] = _source_weight_score(
                successful_sources,
                providers,
                source_weights=source_weights,
            )
            _ensure_context_row_enrichment(
                row,
                requested_sources=providers,
                successful_sources=successful_sources,
                source_weights=source_weights,
            )
            try:
                _write_cache(cache_dir, code, providers, row)
            except Exception as exc:
                errors.append(f"{code} cache: {exc}")
            return row, errors
        return None, errors
    except Exception as exc:
        return None, [*errors, f"{code} context: {exc}"]


def fetch_stock_news_summary(code: str, *, limit: int = 3) -> str:
    """通过 akshare 抓取最近 ``limit`` 条个股新闻并拼接为紧凑文本。"""
    import akshare as ak

    df = ak.stock_news_em(symbol=str(code).zfill(6))
    if df is None or df.empty:
        return ""
    items = []
    for _, row in df.head(max(limit, 1)).iterrows():
        title = _first_value(row, ["新闻标题", "标题", "title"])
        published_at = _first_value(row, ["发布时间", "时间", "date"])
        source = _first_value(row, ["文章来源", "来源", "source"])
        text = " ".join(item for item in [published_at, source, title] if item)
        if text:
            items.append(text)
    return _compress_text(" | ".join(_dedupe(items)), max_len=520)


def fetch_stock_announcement_summary(code: str, *, limit: int = 3) -> str:
    """通过巨潮 cninfo 抓取最近 ``limit`` 条公告并拼接。"""
    import akshare as ak

    # 只看最近 45 天, 公告基本不会更长时效影响当日决策
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")
    df = ak.stock_zh_a_disclosure_report_cninfo(
        symbol=str(code).zfill(6),
        market="沪深京",
        start_date=start,
        end_date=end,
    )
    if df is None or df.empty:
        return ""
    items = []
    for _, row in df.head(max(limit, 1)).iterrows():
        title = _first_value(row, ["公告标题", "标题", "announcementTitle", "title"])
        date = _first_value(row, ["公告时间", "公告日期", "date"])
        if title:
            items.append(" ".join(item for item in [date, title] if item))
    return _compress_text(" | ".join(_dedupe(items)), max_len=520)


def fetch_stock_fund_flow_summary(code: str) -> str:
    """抓取个股最近一日的资金流(主力/超大单/大单净流入等)文本摘要。"""
    import akshare as ak

    market = _market_for_code(code)
    if not market:
        return ""
    df = ak.stock_individual_fund_flow(stock=str(code).zfill(6), market=market)
    if df is None or df.empty:
        return ""
    row = df.iloc[-1]
    fields = []
    for column in df.columns:
        name = str(column)
        # 仅保留常见的有信息量字段, 避免截图式表格被吐进 LLM
        if any(keyword in name for keyword in ["日期", "主力净流入", "超大单净流入", "大单净流入", "净占比"]):
            value = _safe_text(row.get(column))
            if value:
                fields.append(f"{name}={value}")
    return _compress_text("，".join(fields[:8]), max_len=420)


def fetch_stock_quote_summary(code: str) -> str:
    """从腾讯接口取一档实时行情与估值的轻量摘要(供软排序用)。"""
    symbol = _tencent_symbol_for_code(code)
    if not symbol:
        return ""
    resp = requests.get(
        f"https://qt.gtimg.cn/q={symbol}",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=8,
    )
    resp.raise_for_status()
    text = resp.text or ""
    if "=\"" not in text:
        return ""
    body = text.split("=\"", 1)[1].split("\";", 1)[0]
    parts = body.split("~")
    if len(parts) < 46:
        return ""
    # 腾讯接口字段顺序固定, 下标含义见接口文档
    fields = [
        ("名称", _part(parts, 1)),
        ("现价", _part(parts, 3)),
        ("涨跌幅", _part(parts, 32)),
        ("最高", _part(parts, 33)),
        ("最低", _part(parts, 34)),
        ("成交额万元", _part(parts, 37)),
        ("换手率", _part(parts, 38)),
        ("市盈率", _part(parts, 39)),
        ("总市值亿元", _part(parts, 45)),
        ("流通市值亿元", _part(parts, 44)),
    ]
    return _compress_text(
        "，".join(f"{name}={value}" for name, value in fields if value),
        max_len=360,
    )


def classify_context_events(row: dict[str, object]) -> list[str]:
    """从候选上下文文本里提取正面 / 负面事件标签(粗粒度关键词匹配)。"""
    text = _row_text(row)
    tags: list[str] = []
    for label, keywords in _POSITIVE_EVENT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            tags.append(label)
    for label, keywords in _NEGATIVE_EVENT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            tags.append(f"风险:{label}")
    return _dedupe(tags)


def classify_negative_events(row: dict[str, object]) -> list[str]:
    """只提取负面事件分类(便于在过滤阶段拦截)。"""
    text = _row_text(row)
    flags = [
        label
        for label, keywords in _NEGATIVE_EVENT_KEYWORDS.items()
        if any(keyword in text for keyword in keywords)
    ]
    return _dedupe(flags)


def classify_announcement_categories(row: dict[str, object]) -> list[str]:
    """从公告字段中提取粗粒度公告类别(业绩/合同/监管问询等)。"""
    text = " ".join(
        str(row.get(key) or "")
        for key in ("announcement", "announcements")
        if row.get(key)
    )
    categories = [
        label
        for label, keywords in _ANNOUNCEMENT_CATEGORY_KEYWORDS.items()
        if any(keyword in text for keyword in keywords)
    ]
    return _dedupe(categories)


def _ensure_context_row_enrichment(
    row: dict[str, object],
    *,
    requested_sources: list[str] | None = None,
    successful_sources: list[str] | None = None,
    source_weights: dict[str, float] | None = None,
) -> None:
    """保证 row 拥有 source_weight_score、event_tags 等下游 LLM 期望字段。"""
    successful_sources = successful_sources or _successful_sources_from_row(row)
    requested_sources = requested_sources or successful_sources
    if source_weights is not None or not isinstance(row.get("source_weight_score"), (int, float)):
        row["source_weight_score"] = _source_weight_score(
            successful_sources,
            requested_sources,
            source_weights=source_weights,
        )
    if not isinstance(row.get("announcement_categories"), list):
        row["announcement_categories"] = classify_announcement_categories(row)
    if not isinstance(row.get("event_tags"), list):
        row["event_tags"] = classify_context_events(row)
    if not isinstance(row.get("negative_event_flags"), list):
        row["negative_event_flags"] = classify_negative_events(row)
    summary = _safe_text(row.get("context_summary"), max_len=600)
    needs_summary = not summary
    if isinstance(row.get("event_tags"), list) and row["event_tags"] and "事件标签:" not in summary:
        needs_summary = True
    if (
        isinstance(row.get("announcement_categories"), list)
        and row["announcement_categories"]
        and "公告类别:" not in summary
    ):
        needs_summary = True
    if (
        isinstance(row.get("negative_event_flags"), list)
        and row["negative_event_flags"]
        and "负面风险:" not in summary
    ):
        needs_summary = True
    if needs_summary:
        row["context_summary"] = _summarize_row_context(row)


def _market_for_code(code: str) -> str:
    """根据股票代码前缀推测 ``akshare`` 期望的市场字段(sh / sz)。"""
    code = _normalize_code(code)
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    return ""


def _tencent_symbol_for_code(code: str) -> str:
    """把股票代码转成腾讯 qt.gtimg 接口的 ``sh/sz/bj`` 前缀形式。"""
    code = _normalize_code(code)
    if code.startswith(("6", "5", "9")):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8", "920")):
        # 920 开头为北交所; 4/8 是历史北交所代码前缀
        return f"bj{code}"
    return ""


def _part(parts: list[str], index: int) -> str:
    """安全地取腾讯接口字段下标, 越界返回空字符串。"""
    return _safe_text(parts[index] if index < len(parts) else "", max_len=80)


def _first_value(row: pd.Series, columns: list[str]) -> str:
    """从多语言备选列名里挑首个非空值(给不同数据源适配)。"""
    for column in columns:
        if column in row.index:
            value = _safe_text(row.get(column))
            if value:
                return value
    return ""


def _safe_text(value: object, *, max_len: int = 240) -> str:
    """把任意值归一化为短文本, 自动屏蔽 NaN/None/<NA>。"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text[:max_len]


def _normalize_code(value: object) -> str:
    """把可能带 ``.0`` / 含其它字符的代码归一化为 6 位数字字符串。"""
    text = _safe_text(value, max_len=80)
    if not text:
        return ""
    # 去掉 pandas/CSV 常见 ``000001.0`` 形式
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit():
        return text.zfill(6)[-6:]
    # 退路: 从任意字符串中抽取连续 6 位数字(优先放在括号或边界外的)
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if match:
        return match.group(1)
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6)[-6:] if digits else ""


def _normalize_providers(providers: list[str]) -> list[str]:
    """统一 provider 命名, 去重并保留先后顺序(如 ``fundflow`` -> ``fund_flow``)。"""
    aliases = {"announcements": "announcement", "fundflow": "fund_flow"}
    result = []
    seen = set()
    for item in providers:
        key = aliases.get(str(item).strip().lower(), str(item).strip().lower())
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _source_confidence(successful_sources: list[str], requested_sources: list[str]) -> float:
    """把"成功来源 / 请求来源"覆盖率转成 0-1 的 confidence。"""
    requested = set(requested_sources)
    if not requested:
        return 0.0
    coverage = len(set(successful_sources)) / len(requested)
    return round(min(1.0, max(0.0, coverage)), 4)


def _source_weight_score(
    successful_sources: list[str],
    requested_sources: list[str],
    *,
    source_weights: dict[str, float] | None = None,
) -> float:
    """按各来源权重计算加权的"来源可信度", 用作综合打分输入。"""
    requested = _normalize_providers(requested_sources)
    successful = set(_normalize_providers(successful_sources))
    weights = _normalized_source_weights(source_weights)
    total = sum(weights.get(source, 0.5) for source in requested)
    if total <= 0:
        return 0.0
    score = sum(weights.get(source, 0.5) for source in successful if source in requested) / total
    return round(min(1.0, max(0.0, score)), 4)


def _normalized_source_weights(source_weights: dict[str, float] | None) -> dict[str, float]:
    """用户自定义权重覆盖默认权重, 缺失来源保留默认, 权重钳到非负。"""
    result = dict(_SOURCE_WEIGHTS)
    for key, value in (source_weights or {}).items():
        normalized = _normalize_providers([str(key)])
        if not normalized:
            continue
        try:
            result[normalized[0]] = max(float(value), 0.0)
        except (TypeError, ValueError):
            continue
    return result


def _summarize_row_context(row: dict[str, object]) -> str:
    """把 row 的几个素材字段拼成一段"压缩摘要", 供 LLM 一次性消化。"""
    parts = []
    for key, label in (
        ("news", "新闻"),
        ("announcement", "公告"),
        ("fund_flow", "资金流"),
        ("quote", "行情估值"),
    ):
        value = _compress_text(row.get(key), max_len=180)
        if value:
            parts.append(f"{label}:{value}")
    event_tags = row.get("event_tags")
    if isinstance(event_tags, list) and event_tags:
        parts.append("事件标签:" + ",".join(str(item) for item in event_tags[:6]))
    announcement_categories = row.get("announcement_categories")
    if isinstance(announcement_categories, list) and announcement_categories:
        parts.append("公告类别:" + ",".join(str(item) for item in announcement_categories[:6]))
    negative_flags = row.get("negative_event_flags")
    if isinstance(negative_flags, list) and negative_flags:
        parts.append("负面风险:" + ",".join(str(item) for item in negative_flags[:6]))
    return _compress_text("；".join(parts), max_len=520)


def _row_text(row: dict[str, object]) -> str:
    """把 row 里所有可用文本字段拼成一段, 作为关键词匹配的输入。"""
    fields = []
    for key in ("news", "announcement", "announcements", "fund_flow", "quote", "summary", "context", "text"):
        value = row.get(key)
        if value:
            fields.append(str(value))
    return " ".join(fields)


def _successful_sources_from_row(row: dict[str, object]) -> list[str]:
    """从已存在的 row 反推哪些来源拉到了非空文本(用于权重重算)。"""
    sources = []
    if row.get("news"):
        sources.append("news")
    if row.get("announcement") or row.get("announcements"):
        sources.append("announcement")
    if row.get("fund_flow") or row.get("fundflow"):
        sources.append("fund_flow")
    if row.get("quote"):
        sources.append("quote")
    return sources


def _compress_text(value: object, *, max_len: int) -> str:
    """压缩文本到 ``max_len`` 以内, 优先在分隔符处截断, 保留可读性。"""
    text = _safe_text(value, max_len=max(max_len * 2, 240))
    if not text:
        return ""
    text = " ".join(text.replace("\n", " ").split())
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    # 优先在中点之后寻找分隔符截断, 避免把半句话留在末尾
    for delimiter in (" | ", "；", "。", "，", " "):
        idx = cut.rfind(delimiter)
        if idx >= max_len * 0.55:
            return cut[:idx].rstrip() + "..."
    return cut.rstrip() + "..."


def _cache_path(cache_dir: str | Path | None, code: str, providers: list[str]) -> Path | None:
    """按 (代码, 提供方序列) 生成缓存文件路径, 关闭缓存时返回 None。"""
    if cache_dir is None:
        return None
    key = "_".join(providers) or "none"
    return Path(cache_dir) / f"{str(code).zfill(6)}_{key}.json"


def _read_cache(
    cache_dir: str | Path | None,
    code: str,
    providers: list[str],
    *,
    cache_ttl_hours: int,
) -> dict[str, object] | None:
    """读取缓存, 过期或解析失败都视作未命中。"""
    path = _cache_path(cache_dir, code, providers)
    if path is None or not path.is_file() or cache_ttl_hours <= 0:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(str(data.get("cached_at", "")))
        if datetime.now() - cached_at > timedelta(hours=cache_ttl_hours):
            return None
        row = data.get("row")
        return row if isinstance(row, dict) else None
    except Exception:
        # 任何解析异常都视作未命中, 避免脏缓存污染
        return None


def _write_cache(
    cache_dir: str | Path | None,
    code: str,
    providers: list[str],
    row: dict[str, object],
) -> None:
    """把 row 序列化写入缓存文件, 创建目录(自动创建父目录)。"""
    path = _cache_path(cache_dir, code, providers)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cached_at": datetime.now().isoformat(), "row": row}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _dedupe(items: list[str]) -> list[str]:
    """按内容去重, 保留首次出现的非空元素。"""
    seen = set()
    result = []
    for item in items:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


