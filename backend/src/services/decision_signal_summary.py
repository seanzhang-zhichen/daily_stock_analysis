# -*- coding: utf-8 -*-
"""用于通知与风险视图的低敏感度 DecisionSignal 摘要。

本模块提供 DecisionSignal 的低敏感度摘要生成与格式化功能，
主要用于通知文案和风险视图展示，避免暴露敏感信息。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.utils.sanitize import sanitize_decision_signal_payload, sanitize_decision_signal_text


# 摘要字段列表：定义了 DecisionSignal 摘要中需要包含的字段
SUMMARY_FIELDS = (
    "id",
    "stock_code",
    "stock_name",
    "market",
    "action",
    "action_label",
    "horizon",
    "status",
    "source_type",
    "source_report_id",
    "reason",
    "watch_conditions",
    "risk_summary",
    "created_at",
    "expires_at",
)


def summarize_decision_signal(item: Any) -> Optional[Dict[str, Any]]:
    """从序列化后的 DecisionSignal 条目生成低敏感度摘要。

    参数:
        item: 序列化后的 DecisionSignal 条目，预期为字典类型。

    返回:
        包含摘要字段的字典；如果输入不是字典或摘要为空，则返回 None。
    """
    # 输入校验：仅处理字典类型的条目
    if not isinstance(item, dict):
        return None
    summary: Dict[str, Any] = {}
    # 遍历预定义字段，提取并清理有效值
    for field_name in SUMMARY_FIELDS:
        value = item.get(field_name)
        # 跳过空值（None、空字符串、空列表、空字典）
        if value in (None, "", [], {}):
            continue
        summary[field_name] = sanitize_decision_signal_payload(value)
    return summary or None


def format_decision_signal_excerpt(summary: Any, report_language: str = "zh") -> str:
    """把摘要格式化为通知文案里紧凑的公开 DecisionSignal 摘录。

    参数:
        summary: 摘要字典，包含 action_label、horizon、source_report_id 等字段。
        report_language: 报告语言，支持 "zh"（中文）和 "en"（英文），默认中文。

    返回:
        格式化的 Markdown 文本字符串，用于通知展示。
    """
    # 校验输入有效性
    if not isinstance(summary, dict) or not summary:
        return ""
    # 根据 report_language 确定语言，默认中文
    language = "en" if str(report_language or "").lower().startswith("en") else "zh"
    # 定义中英文标签映射
    labels = {
        "zh": {
            "heading": "AI 决策信号",
            "action": "动作",
            "horizon": "周期",
            "reason": "理由",
            "watch_conditions": "观察条件",
            "risk_summary": "风险",
            "source_report_id": "报告",
        },
        "en": {
            "heading": "AI decision signal",
            "action": "Action",
            "horizon": "Horizon",
            "reason": "Reason",
            "watch_conditions": "Watch",
            "risk_summary": "Risk",
            "source_report_id": "Report",
        },
    }[language]

    # 构建摘要头部信息（动作、周期、报告编号）
    parts = []
    action_label = _public_scalar(summary.get("action_label") or summary.get("action"), max_length=32)
    if action_label:
        parts.append(f"{labels['action']}: {action_label}")
    horizon = _public_scalar(summary.get("horizon"), max_length=16)
    if horizon:
        parts.append(f"{labels['horizon']}: {horizon}")
    source_report_id = _public_scalar(summary.get("source_report_id"), max_length=24)
    if source_report_id:
        parts.append(f"{labels['source_report_id']}: #{source_report_id}")

    # 组装输出文本
    lines = [f"**{labels['heading']}**"]
    if parts:
        lines.append(" | ".join(parts))
    # 追加理由、观察条件、风险摘要等详细内容
    for key in ("reason", "watch_conditions", "risk_summary"):
        max_length = None if key == "reason" else 120
        text = _public_text(summary.get(key), max_length=max_length)
        if text:
            lines.append(f"- {labels[key]}: {text}")
    return "\n".join(lines)


def _public_scalar(value: Any, *, max_length: int) -> str:
    """把标量值安全清理后按最大长度截断。

    参数:
        value: 输入的标量值。
        max_length: 最大允许长度。

    返回:
        清理并截断后的字符串；空值返回空字符串。
    """
    if value in (None, ""):
        return ""
    return sanitize_decision_signal_text(value)[:max_length]


def _public_text(value: Any, *, max_length: Optional[int]) -> str:
    """把任意形态的公开文本（标量/列表/字典）安全清理后按长度截断。

    参数:
        value: 输入值，可以是标量、列表或字典。
        max_length: 最大允许长度；None 表示不限制。

    返回:
        清理后的字符串；空值返回空字符串。
    """
    if value in (None, "", [], {}):
        return ""
    # 列表/元组：用中文分号连接各元素
    if isinstance(value, (list, tuple)):
        text = "；".join(str(item).strip() for item in value if str(item or "").strip())
    # 字典：格式化为 "key: value" 并用分号连接
    elif isinstance(value, dict):
        text = "；".join(
            f"{key}: {item}"
            for key, item in value.items()
            if str(key or "").strip() and str(item or "").strip()
        )
    # 其他类型：直接转为字符串
    else:
        text = str(value).strip()
    sanitized = sanitize_decision_signal_text(text)
    return sanitized if max_length is None else sanitized[:max_length]
