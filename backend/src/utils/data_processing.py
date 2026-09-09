# -*- coding: utf-8 -*-
"""
共享的数据解析与规范化工具。

为基本面上下文、市场结构、板块信息等结构化字段提供：
- JSON 字符串最佳努力解析
- 模型占位值归一化（如 "unknown"、"none"）
- 板块/板块排名/财务报告/分红等字段清洗
- 从持久化快照中按 schema 提取 API 友好的字段子集

供分析服务、API 响应组装、上下文快照反序列化层复用。
"""

import json
from typing import Any, Dict, List, Optional


# 模型占位/错误占位值集合，统一归一化为 None
_MODEL_PLACEHOLDER_VALUES = {"unknown", "error", "none", "null", "n/a"}


def normalize_model_used(value: Any) -> Optional[str]:
    """把占位/空模型值规范化为 None。

    Args:
        value: 任意输入（None / 字符串 / 其他类型）。

    Returns:
        - None 表示该字段为空或属于占位值
        - 去除首尾空白后的字符串
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # 大小写不敏感地比对预定义占位词表
    if text.lower() in _MODEL_PLACEHOLDER_VALUES:
        return None
    return text


def parse_json_field(value: Any) -> Any:
    """尽力而为地解析字符串字段为 JSON，非字符串则原样透传。

    Args:
        value: 待解析值。

    Returns:
        解析后的 Python 对象；解析失败或非字符串输入则原样返回。
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return value
    return value


def _non_empty_dict(value: Any) -> Optional[Dict[str, Any]]:
    """仅当值是 dict 且非空时返回该 dict，其余情况返回 None。"""
    if not isinstance(value, dict):
        return None
    return value if value else None


def _normalize_belong_boards(value: Any) -> List[Dict[str, Any]]:
    """把所属板块列表归一化为稳定的 `{name, code?, type?}` 字典列表。

    Args:
        value: 原始所属板块字段，可能为 None / list / 其他类型。

    Returns:
        清洗后的字典列表；缺失 `name` 的项会被丢弃。
    """
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name is None:
            continue
        name_text = str(name).strip()
        if not name_text:
            continue
        board = {"name": name_text}
        if item.get("code") is not None:
            code_text = str(item.get("code")).strip()
            if code_text:
                board["code"] = code_text
        if item.get("type") is not None:
            type_text = str(item.get("type")).strip()
            if type_text:
                board["type"] = type_text
        normalized.append(board)
    return normalized


def _safe_float(value: Any) -> Optional[float]:
    """把数字或带百分号的字符串解析为 float，失败返回 None。"""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            # 兼容 "1.23%" 形式的字符串，先去掉尾部 %
            if text.endswith("%"):
                text = text[:-1].strip()
            return float(text)
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_sector_ranking_items(value: Any) -> List[Dict[str, Any]]:
    """清洗板块涨跌幅排行行，仅保留 `name` 与 `change_pct` 等有效字段。"""
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name is None:
            continue
        name_text = str(name).strip()
        if not name_text:
            continue
        ranking_item: Dict[str, Any] = {"name": name_text}
        change_pct = _safe_float(item.get("change_pct"))
        if change_pct is not None:
            ranking_item["change_pct"] = change_pct
        normalized.append(ranking_item)
    return normalized


def _normalize_sector_rankings(value: Any) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """从基本面上下文的板块块中归一化 top / bottom 排行。"""
    if not isinstance(value, dict):
        return None

    return {
        "top": _normalize_sector_ranking_items(value.get("top")),
        "bottom": _normalize_sector_ranking_items(value.get("bottom")),
    }


def extract_fundamental_context(
    context_snapshot: Any,
    fallback_fundamental_payload: Any = None,
) -> Optional[Dict[str, Any]]:
    """从 context snapshot 中解析 `fundamental_context`，必要时回退到备选 payload。

    Args:
        context_snapshot: 持久化的 JSON 快照（字符串或 dict）。
        fallback_fundamental_payload: snapshot 不可用时的回退 payload。

    Returns:
        归一化后的 fundamental 字典；全部失败返回 None。
    """
    snapshot_obj = parse_json_field(context_snapshot)
    if isinstance(snapshot_obj, dict):
        enhanced = snapshot_obj.get("enhanced_context")
        if isinstance(enhanced, dict):
            fundamental = enhanced.get("fundamental_context")
            if isinstance(fundamental, dict):
                return fundamental

    fallback_obj = parse_json_field(fallback_fundamental_payload)
    if isinstance(fallback_obj, dict):
        return fallback_obj
    return None


def extract_fundamental_detail_fields(
    context_snapshot: Any,
    fallback_fundamental_payload: Any = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """从 `fundamental_context` 提取稳定的财报与分红字段子集，供 API 层返回。

    Args:
        context_snapshot: 持久化的 JSON 快照。
        fallback_fundamental_payload: snapshot 不可用时的回退 payload。

    Returns:
        形如 `{"financial_report": {...} | None, "dividend_metrics": {...} | None}` 的字典。
    """
    fundamental_ctx = extract_fundamental_context(
        context_snapshot=context_snapshot,
        fallback_fundamental_payload=fallback_fundamental_payload,
    )
    if not isinstance(fundamental_ctx, dict):
        return {"financial_report": None, "dividend_metrics": None}

    earnings_block = fundamental_ctx.get("earnings")
    earnings_data = earnings_block.get("data") if isinstance(earnings_block, dict) else None
    if not isinstance(earnings_data, dict):
        return {"financial_report": None, "dividend_metrics": None}

    financial_report = _non_empty_dict(earnings_data.get("financial_report"))
    dividend_metrics = _non_empty_dict(earnings_data.get("dividend"))
    return {
        "financial_report": financial_report,
        "dividend_metrics": dividend_metrics,
    }


def extract_board_detail_fields(
    context_snapshot: Any,
    fallback_fundamental_payload: Any = None,
) -> Dict[str, Any]:
    """从 `fundamental_context` 提取稳定的板块详情字段（所属板块、板块排行）。"""
    fundamental_ctx = extract_fundamental_context(
        context_snapshot=context_snapshot,
        fallback_fundamental_payload=fallback_fundamental_payload,
    )
    if not isinstance(fundamental_ctx, dict):
        return {"belong_boards": [], "sector_rankings": None}

    boards_block = fundamental_ctx.get("boards")
    sector_rankings = None
    if isinstance(boards_block, dict):
        # 仅在板块块状态正常（ok / partial / 未设置）时取出 data，避免把明显错误的数据传递给前端
        boards_status = boards_block.get("status")
        if boards_status in {"ok", "partial"} or boards_status is None:
            sector_rankings = boards_block.get("data")
    return {
        "belong_boards": _normalize_belong_boards(fundamental_ctx.get("belong_boards")),
        "sector_rankings": _normalize_sector_rankings(sector_rankings),
    }


def extract_market_structure_context(context_snapshot: Any) -> Optional[Dict[str, Any]]:
    """从持久化快照中提取带版本的市场结构数据块。"""
    snapshot_obj = parse_json_field(context_snapshot)
    if not isinstance(snapshot_obj, dict):
        return None
    enhanced = snapshot_obj.get("enhanced_context")
    if isinstance(enhanced, dict):
        value = enhanced.get("market_structure_context")
        if isinstance(value, dict):
            return value
    value = snapshot_obj.get("market_structure_context")
    return value if isinstance(value, dict) else None
