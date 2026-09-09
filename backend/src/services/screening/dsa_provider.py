# -*- coding: utf-8 -*-
# 派生自 AlphaSift (commit 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf)，
# 遵循 Apache-2.0 协议并适配本仓库。
"""DSA provider 上下文桥接。

本模块消费 ``context["dsa"]`` 透传的 DSA 可调用对象。设计上尽力而为：
当 DSA 可用时筛选用到更丰富的数据，但单一 provider 慢或坏时筛选取
仍能继续完成。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from src.services.screening.models import Pick

logger = logging.getLogger(__name__)

DSA_PROVIDER_MAX_CANDIDATES = 5


def apply_dsa_provider_context(
    picks: list[Pick],
    context: dict[str, Any] | None,
    *,
    max_candidates: int | None = None,
) -> list[str]:
    """在进入 LLM 排序之前，把 DSA provider 富化后的上下文挂到头部候选上。

    行为是尽力而为：context 里没有 DSA provider 或调用失败时，函数直接返回空列表，
    不会影响后续排序链路。

    Args:
        picks: 筛选取的候选 Pick 列表（按优先级排序，前若干条会被富化）。
        context: 调用方透传的上下文字典，从 ``context["dsa"]`` 读取 provider 集合。
        max_candidates: 头部富化的候选上限；缺省沿用 provider 配置或全局默认值。

    Returns:
        给到上层 LLM 的若干提示文本，包含成功/失败统计。
    """
    provider = _extract_provider_context(context)
    # 既无候选也无 provider 时直接返回, 不污染 notes; 这是"零开销"分支。
    if not picks or not provider:
        return []

    limit = min(len(picks), max(_resolve_max_candidates(provider, max_candidates), 0))
    if limit <= 0:
        return []

    enriched_count = 0
    errors: list[str] = []
    for pick in picks[:limit]:
        try:
            payload = _fetch_candidate_context(provider, pick)
            if not payload:
                continue
            normalized = _normalize_candidate_payload(payload, pick)
            if not normalized:
                continue
            pick.dsa_context = normalized["context"]
            pick.dsa_news = normalized["news"]
            pick.dsa_analysis_summary = normalized["summary"]
            if _is_enriched_context(pick.dsa_context, pick.dsa_news):
                enriched_count += 1
        except Exception as exc:  # noqa: BLE001 - external DSA providers are optional.
            message = f"{pick.code}: {exc}"
            errors.append(message)
            pick.dsa_context = {
                "enriched": False,
                "warnings": [message],
            }
            logger.warning("DSA provider context failed for %s: %s", pick.code, exc)

    notes = [f"DSA provider context applied {enriched_count} of {limit} candidates"]
    if errors:
        sample = " | ".join(errors[:5])
        suffix = f" | +{len(errors) - 5} more" if len(errors) > 5 else ""
        notes.append(f"DSA provider context row errors: {sample}{suffix}")
    return notes


def _resolve_max_candidates(provider: dict[str, Any], max_candidates: int | None) -> int:
    """根据显式入参 / provider 配置 / 全局默认值三层降级确定富化上限。"""
    if max_candidates is not None:
        return max_candidates
    configured = provider.get("max_candidates")
    try:
        if configured is None or configured == "":
            raise ValueError
        return int(configured)
    except (TypeError, ValueError):
        return DSA_PROVIDER_MAX_CANDIDATES


def _extract_provider_context(context: dict[str, Any] | None) -> dict[str, Any]:
    """安全地从 context 中取出 ``dsa`` 子字段，类型不匹配时返回空字典。"""
    if not isinstance(context, dict):
        return {}
    provider = context.get("dsa")
    return provider if isinstance(provider, dict) else {}


def _fetch_candidate_context(provider: dict[str, Any], pick: Pick) -> dict[str, Any]:
    """按 provider 能力自动选取最丰富的调用路径。

    优先使用 ``get_candidate_context`` 这种聚合入口；缺失时降级到分别调用
    实时行情 / 基本面 / 资讯三个 provider，组装成统一 payload。
    """
    candidate_getter = provider.get("get_candidate_context")
    if callable(candidate_getter):
        payload = _call_candidate_getter(candidate_getter, pick)
        return payload if isinstance(payload, dict) else {}

    quote = _call_optional_provider(provider.get("get_realtime_quote"), pick.code)
    fundamentals = _call_optional_provider(provider.get("get_fundamental_context"), pick.code)
    news = _call_news_provider(provider.get("search_stock_news"), pick)
    return {
        "enriched": bool(quote or fundamentals or _news_results(news)),
        "quote": quote,
        "fundamentals": fundamentals,
        "news": news,
        "warnings": [],
    }


def _call_candidate_getter(getter: Callable[..., Any], pick: Pick) -> Any:
    """调用 DSA 聚合入口；不同 provider 签名不同，先尝试两个参数再退回单参数。"""
    try:
        return getter(pick.code, pick.name)
    except TypeError:
        return getter(pick.code)


def _call_optional_provider(provider: Any, stock_code: str) -> dict[str, Any]:
    """调用可选 provider；不可调用或返回非字典时统一当作"无数据"。"""
    if not callable(provider):
        return {}
    payload = provider(stock_code)
    return payload if isinstance(payload, dict) else {}


def _call_news_provider(provider: Any, pick: Pick) -> dict[str, Any]:
    """调用资讯 provider；按三档兼容：不带 max_results、双参、单参。"""
    if not callable(provider):
        return {"success": False, "results": []}
    try:
        payload = provider(pick.code, pick.name, max_results=3)
    except TypeError:
        try:
            payload = provider(pick.code, pick.name)
        except TypeError:
            payload = provider(pick.code)
    return payload if isinstance(payload, dict) else {"success": False, "results": []}


def _normalize_candidate_payload(payload: dict[str, Any], pick: Pick) -> dict[str, Any]:
    """归一化 DSA payload，提取 context / news / summary 三段供 Pick 使用。

    兼容两种结构：直接给扁平字段、或顶层 ``dsa_context`` 是聚合对象。
    summary 缺失时按 context + news 自动拼装。
    """
    full_payload = payload
    context = payload.get("dsa_context") if isinstance(payload.get("dsa_context"), dict) else payload
    if not isinstance(context, dict):
        return {}

    news = payload.get("dsa_news")
    if not isinstance(news, list):
        news = _news_results(context.get("news"))
    news = [item for item in news if isinstance(item, dict)]

    summary = str(payload.get("dsa_analysis_summary") or "").strip()
    if not summary:
        summary = _build_dsa_summary(pick, context, news)

    normalized_context = dict(context)
    normalized_context.setdefault("enriched", _is_enriched_context(normalized_context, news))
    if full_payload is not context and "dsa_context" in full_payload:
        normalized_context.setdefault("source_payload", "dsa_candidate_context")
    return {
        "context": normalized_context,
        "news": news,
        "summary": summary,
    }


def _is_enriched_context(context: dict[str, Any], news: list[dict[str, Any]]) -> bool:
    """判断一次 context 是否实质带数据：任何一个数据维度非空即视为"已富化"。"""
    return bool(
        context.get("enriched")
        or context.get("quote")
        or context.get("fundamentals")
        or news
    )


def _news_results(news_payload: Any) -> list[dict[str, Any]]:
    """从资讯 payload 中抽取 ``results`` 列表，统一过滤掉非字典项。"""
    if isinstance(news_payload, dict) and isinstance(news_payload.get("results"), list):
        return [item for item in news_payload["results"] if isinstance(item, dict)]
    if isinstance(news_payload, list):
        return [item for item in news_payload if isinstance(item, dict)]
    return []


def _build_dsa_summary(pick: Pick, context: dict[str, Any], news: list[dict[str, Any]]) -> str:
    """当 provider 没显式给 ``dsa_analysis_summary`` 时，按行情/基本面/资讯拼一段简短摘要。

    摘要面向 LLM，使用中文短句便于 prompt 直接拼接。
    """
    parts: list[str] = []
    quote = context.get("quote") if isinstance(context.get("quote"), dict) else {}
    # 行情字段缺失时退回到 Pick 自带的现价/涨跌幅, 至少保证摘要不为空
    price = quote.get("price") if quote else pick.price
    change_pct = quote.get("change_pct") if quote else pick.change_pct
    if price not in (None, ""):
        text = f"DSA行情: 现价 {price}"
        if change_pct not in (None, ""):
            text += f", 涨跌幅 {change_pct}%"
        parts.append(text)

    fundamentals = context.get("fundamentals")
    coverage = fundamentals.get("coverage") if isinstance(fundamentals, dict) else {}
    if isinstance(coverage, dict):
        available = [
            str(key)
            for key, value in coverage.items()
            if str(value).lower() in {"available", "partial"}
        ]
        if available:
            parts.append(f"DSA基本面覆盖: {', '.join(available[:4])}")

    titles = [
        str(item.get("title") or "").strip()
        for item in news
        if isinstance(item.get("title"), str) and item.get("title")
    ]
    if titles:
        parts.append(f"DSA新闻: {'; '.join(titles[:2])}")
    return "; ".join(parts)


