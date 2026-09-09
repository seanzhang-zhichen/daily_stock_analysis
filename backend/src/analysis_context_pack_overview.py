# -*- coding: utf-8 -*-
"""AnalysisContextPack 低敏公开概览（Issue #1389 P4 用）。

负责把完整的 ``AnalysisContextPack`` 投影为只包含状态/标签/告警等摘要信息
的公开结构，供前端 P4 仪表盘或外部 API 消费。所有敏感字段（api_key、token 等）
在写入前会被替换为 ``[REDACTED]``，原始载荷与详细数值不会进入本结构。

主要能力：
- 渲染实时 pack 为低敏概览（按语言产出 block 标签）
- 从持久化 context_snapshot 中提取历史概览
- 在 API 返回前从快照中移除其他单独暴露的摘要字段（避免重复下发）
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from src.analysis_context_pack_prompt import (
    SENSITIVE_MARKERS,
    analysis_context_pack_to_dict,
    get_analysis_context_pack_block_labels,
    iter_analysis_context_pack_block_keys,
)
from src.market_phase_summary import MARKET_PHASE_SUMMARY_KEY
from src.schemas.analysis_context_pack import ContextFieldStatus


# 该字段作为持久化 context_snapshot 中的概览入口键名，前后端需保持一致。
ANALYSIS_CONTEXT_PACK_OVERVIEW_KEY = "analysis_context_pack_overview"
# pack 中所有可能的状态枚举。
_ALL_STATUSES = tuple(status.value for status in ContextFieldStatus)
# 数据质量打分的核心 block 集合，用于过滤来历不明的键。
_DATA_QUALITY_BLOCK_KEYS = {"quote", "daily_bars", "technical", "news", "fundamentals", "chip"}
logger = logging.getLogger(__name__)


def render_analysis_context_pack_overview(
    pack: Any,
    *,
    report_language: str = "zh",
) -> Optional[Dict[str, Any]]:
    """将 AnalysisContextPack 投影为公开的低敏概览。

    返回结构包含 pack 版本、subject、blocks 摘要、状态计数、data_quality
    以及精简后的 metadata。任何字段值如果命中敏感标记都会被替换为
    ``[REDACTED]``，异常情况下返回 ``None``。
    """
    try:
        payload = analysis_context_pack_to_dict(pack)
        subject = payload.get("subject")
        blocks = payload.get("blocks")
        if not isinstance(subject, Mapping) or not isinstance(blocks, Mapping):
            return None

        labels = get_analysis_context_pack_block_labels(report_language)
        overview_blocks: List[Dict[str, Any]] = []
        counts = {status: 0 for status in _ALL_STATUSES}

        for key in iter_analysis_context_pack_block_keys(blocks):
            block = blocks.get(key)
            if not isinstance(block, Mapping):
                continue
            status = _safe_status(block.get("status"))
            if status is None:
                continue

            counts[status] += 1
            overview_blocks.append(
                {
                    "key": _safe_text(key),
                    "label": labels.get(key, _safe_text(key)),
                    "status": status,
                    # block 自身 source 缺失时回退到首个 item 的 source。
                    "source": _first_non_empty(
                        block.get("source"),
                        _first_item_field(block.get("items"), "source"),
                    ),
                    "warnings": _list_strings(block.get("warnings")),
                    "missing_reasons": _item_missing_reasons(block.get("items")),
                }
            )

        if not overview_blocks:
            return None

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
        return {
            "pack_version": _safe_text(payload.get("pack_version")) or "1.0",
            "created_at": _safe_text(payload.get("created_at")) or None,
            "subject": {
                "code": _safe_text(subject.get("code")),
                "stock_name": _safe_text(subject.get("stock_name")) or None,
                "market": _safe_text(subject.get("market")) or None,
            },
            "blocks": overview_blocks,
            "counts": counts,
            "data_quality": _sanitize_data_quality(payload.get("data_quality")),
            "warnings": _list_strings(_nested(payload, "data_quality", "warnings")),
            "metadata": {
                "trigger_source": _safe_text(metadata.get("trigger_source")) or None,
                "news_result_count": _safe_int(metadata.get("news_result_count")),
            },
        }
    except Exception as exc:
        # 概览失败不能影响主链路，仅 debug 日志便于排查。
        logger.debug("render analysis context pack overview failed: %s", exc, exc_info=True)
        return None


def extract_analysis_context_pack_overview(context_snapshot: Any) -> Optional[Dict[str, Any]]:
    """从 context_snapshot 中提取并校验已持久化的公开概览。

    接受 dict 或 JSON 字符串；任何结构异常或缺失 subject_code 都视为无效。
    """
    snapshot = _as_mapping(context_snapshot)
    if not snapshot:
        return None
    overview = snapshot.get(ANALYSIS_CONTEXT_PACK_OVERVIEW_KEY)
    if not isinstance(overview, Mapping):
        return None
    return _sanitize_persisted_overview(overview)


def sanitize_context_snapshot_for_api(context_snapshot: Any) -> Any:
    """返回移除了单独暴露的公开摘要字段的 context_snapshot。

    概览、市场阶段摘要、每日市场上下文摘要、自选组合上下文等本身已经
    通过专门接口下发，因此 API 中不再重复暴露，避免泄露与冗余。
    """
    snapshot = _as_mapping(context_snapshot)
    if snapshot is not None:
        sanitized = dict(snapshot)
        sanitized.pop(ANALYSIS_CONTEXT_PACK_OVERVIEW_KEY, None)
        sanitized.pop(MARKET_PHASE_SUMMARY_KEY, None)
        sanitized.pop("daily_market_context_summary", None)
        sanitized.pop("portfolio_context", None)
        # 同样的摘要字段也可能嵌在 enhanced_context 中，需要一并清理。
        enhanced_context = sanitized.get("enhanced_context")
        if isinstance(enhanced_context, Mapping):
            safe_enhanced_context = dict(enhanced_context)
            safe_enhanced_context.pop("daily_market_context_summary", None)
            safe_enhanced_context.pop("portfolio_context", None)
            sanitized["enhanced_context"] = safe_enhanced_context
        return sanitized
    return context_snapshot


def _as_mapping(value: Any) -> Optional[Mapping[str, Any]]:
    """把输入统一为 Mapping，必要时尝试 JSON 反序列化。"""
    if isinstance(value, Mapping):
        return value
    # 字符串形态的快照先尝试解析，便于从数据库序列化列中恢复。
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _sanitize_persisted_overview(overview: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """校验并清洗已持久化的概览，确保结构合法后再下发。"""
    subject = overview.get("subject")
    blocks = overview.get("blocks")
    if not isinstance(subject, Mapping) or not isinstance(blocks, list):
        return None

    subject_code = _safe_text(subject.get("code"))
    if not subject_code:
        return None

    overview_blocks: List[Dict[str, Any]] = []
    counts = {status: 0 for status in _ALL_STATUSES}
    for block in blocks:
        if not isinstance(block, Mapping):
            return None

        key = _safe_text(block.get("key"))
        status = _safe_status(block.get("status"))
        if not key or status is None:
            return None

        counts[status] += 1
        overview_blocks.append(
            {
                "key": key,
                "label": _safe_text(block.get("label")) or key,
                "status": status,
                "source": _safe_text(block.get("source")) or None,
                "warnings": _list_strings(block.get("warnings")),
                "missing_reasons": _list_strings(block.get("missing_reasons"), limit=3),
            }
        )

    if not overview_blocks:
        return None

    metadata = overview.get("metadata") if isinstance(overview.get("metadata"), Mapping) else {}
    sanitized = {
        "pack_version": _safe_text(overview.get("pack_version")) or "1.0",
        "created_at": _safe_text(overview.get("created_at")) or None,
        "subject": {
            "code": subject_code,
            "stock_name": _safe_text(subject.get("stock_name")) or None,
            "market": _safe_text(subject.get("market")) or None,
        },
        "blocks": overview_blocks,
        "counts": counts,
        "warnings": _list_strings(overview.get("warnings")),
        "metadata": {
            "trigger_source": _safe_text(metadata.get("trigger_source")) or None,
            "news_result_count": _safe_int(metadata.get("news_result_count")),
        },
    }
    # 仅在原始快照中显式携带 data_quality 时才下发，避免凭空编造字段。
    if "data_quality" in overview:
        sanitized["data_quality"] = _sanitize_data_quality(overview.get("data_quality"))
    return sanitized


def _sanitize_data_quality(value: Any) -> Optional[Dict[str, Any]]:
    """清洗 data_quality 字段，仅保留合法整数评分与受限的 limitations。"""
    if not isinstance(value, Mapping):
        return None
    return {
        "overall_score": _safe_score(value.get("overall_score")),
        "level": _safe_quality_level(value.get("level")),
        "block_scores": _safe_block_scores(value.get("block_scores")),
        "limitations": _list_strings(value.get("limitations"), limit=5),
    }


def _safe_status(value: Any) -> Optional[str]:
    """把输入清洗为合法的 ContextFieldStatus 字符串，否则返回 None。"""
    text = _safe_text(value)
    return text if text in _ALL_STATUSES else None


def _safe_quality_level(value: Any) -> Optional[str]:
    """校验数据质量等级枚举值，仅在白名单内返回。"""
    text = _safe_text(value)
    return text if text in {"good", "usable", "limited", "poor"} else None


def _safe_score(value: Any) -> Optional[int]:
    """将 0-100 的整数评分安全转换为 int，非 int 或越界返回 None。"""
    # bool 是 int 的子类但语义不同，需显式排除避免 True/False 被当作 1/0。
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if 0 <= value <= 100:
        return value
    return None


def _safe_block_scores(value: Any) -> Dict[str, int]:
    """只保留属于核心数据块且评分合法的条目。"""
    if not isinstance(value, Mapping):
        return {}
    result: Dict[str, int] = {}
    for key, score in value.items():
        text_key = _safe_text(key)
        safe_score = _safe_score(score)
        if text_key in _DATA_QUALITY_BLOCK_KEYS and safe_score is not None:
            result[text_key] = safe_score
    return result


def _safe_text(value: Any) -> str:
    """把任意值清洗为安全的展示文本；命中敏感标记时返回占位。"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    lowered = text.lower()
    # 包含 api_key/secret/token 等关键字的内容统一脱敏，避免泄露密钥。
    if any(marker in lowered for marker in SENSITIVE_MARKERS):
        return "[REDACTED]"
    return text


def _list_strings(value: Any, *, limit: int = 5) -> List[str]:
    """把输入清洗为去重且限长的字符串列表。"""
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = _safe_text(item)
        if text and text not in result:
            result.append(text)
    return result[:limit]


def _first_non_empty(*values: Any) -> Optional[str]:
    """返回首个非空（经脱敏处理）字符串，全部为空则返回 None。"""
    for value in values:
        text = _safe_text(value)
        if text:
            return text
    return None


def _first_item_field(items: Any, field: str) -> Optional[str]:
    """在 items 字典中查找首个指定字段非空的项并返回该字段值。"""
    if not isinstance(items, Mapping):
        return None
    for item in items.values():
        if not isinstance(item, Mapping):
            continue
        value = _safe_text(item.get(field))
        if value:
            return value
    return None


def _item_missing_reasons(items: Any) -> List[str]:
    """收集各条目中非空且去重的缺失原因，最多 3 条。"""
    if not isinstance(items, Mapping):
        return []
    reasons: List[str] = []
    for item in items.values():
        if not isinstance(item, Mapping):
            continue
        reason = _safe_text(item.get("missing_reason"))
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons[:3]


def _nested(value: Any, *keys: str) -> Any:
    """按顺序在嵌套字典中逐层取值，路径上任何非 Mapping 直接返回 None。"""
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _safe_int(value: Any) -> Optional[int]:
    """把值安全转换为 int，bool 或其他类型均视为无效返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
