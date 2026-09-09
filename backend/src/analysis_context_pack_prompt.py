# -*- coding: utf-8 -*-
"""AnalysisContextPack Prompt 渲染与运行期摘要（Issue #1389 用）。

本模块负责把 ``AnalysisContextPack`` 渲染成只包含状态/告警/缺少数值等
摘要信息的 LLM Prompt 片段，供下游 P3 决策模型使用。**只输出低敏摘要**，
不会复述原始载荷、新闻正文或带密钥/令牌的字段。

主要能力：
- 报告语言归一化（含把韩文复用英文结构）
- 按语言提供 block 标签、状态标签、质量等级标签
- 渲染中文 / 英文版 Prompt 摘要段（含数据限制、置信度与安全规则）
- 解析 pack 为字典（含 Pydantic model_dump 回退路径）
- 通用清洗与脱敏工具（_safe_text/_list_strings/_first_non_empty 等）
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional


# 各数据块的展示标签（中文）。
BLOCK_LABELS_ZH = {
    "quote": "行情",
    "daily_bars": "日线",
    "technical": "技术",
    "chip": "筹码",
    "fundamentals": "基本面",
    "news": "新闻",
}

# 各数据块的展示标签（英文）。
BLOCK_LABELS_EN = {
    "quote": "quote",
    "daily_bars": "daily bars",
    "technical": "technical",
    "chip": "chip",
    "fundamentals": "fundamentals",
    "news": "news",
}

# 状态枚举到中文展示文本的映射。
STATUS_LABELS_ZH = {
    "available": "可用",
    "missing": "缺失",
    "not_supported": "不支持",
    "fallback": "降级",
    "stale": "过期",
    "estimated": "估算",
    "partial": "部分可用",
    "fetch_failed": "抓取失败",
}

# 状态枚举到英文展示文本的映射。
STATUS_LABELS_EN = {
    "available": "available",
    "missing": "missing",
    "not_supported": "not supported",
    "fallback": "fallback",
    "stale": "stale",
    "estimated": "estimated",
    "partial": "partial",
    "fetch_failed": "fetch failed",
}

# 数据质量等级到中文展示文本的映射。
QUALITY_LEVEL_LABELS_ZH = {
    "good": "良好",
    "usable": "可用",
    "limited": "受限",
    "poor": "较差",
}

# 数据质量等级到英文展示文本的映射。
QUALITY_LEVEL_LABELS_EN = {
    "good": "good",
    "usable": "usable",
    "limited": "limited",
    "poor": "poor",
}

# 「核心」数据块中被视为降级的状态集合，用于触发置信度/分析规则提示。
CORE_DEGRADED_STATUSES = {
    "stale",
    "fallback",
    "missing",
    "fetch_failed",
    "partial",
    "estimated",
}

# 已识别的市场阶段枚举，供运行期判断使用。
KNOWN_MARKET_PHASES = frozenset(
    {
        "premarket",
        "intraday",
        "lunch_break",
        "closing_auction",
        "postmarket",
        "non_trading",
        "unknown",
    }
)

# 盘中阶段的细分（连续交易、午休、收盘集合竞价）。
INTRADAY_MARKET_PHASES = frozenset({"intraday", "lunch_break", "closing_auction"})
# 偏保守口径的阶段（非交易、未知），需要更克制地描述数据。
CONSERVATIVE_MARKET_PHASES = frozenset({"non_trading", "unknown"})

# 命中即视为敏感字段需脱敏的子串列表，统一维护避免遗漏。
SENSITIVE_MARKERS = (
    "api_key",
    "access_token",
    "refresh_token",
    "authorization",
    "webhook",
    "password",
    "cookie",
    "secret",
    "token",
    "sendkey",
    "license_key",
)


def normalize_analysis_context_pack_language(report_language: str = "zh") -> str:
    """将报告语言归一化为分析上下文包使用的语言键（zh / en）。"""
    # 韩文复用英文结构标签；模型通过输出语言指令被约束为韩文。
    return "en" if str(report_language or "").lower() in {"en", "ko"} else "zh"


def get_analysis_context_pack_block_labels(report_language: str = "zh") -> Dict[str, str]:
    """按报告语言返回各数据块的展示标签（中文/英文）。"""
    return (
        BLOCK_LABELS_EN
        if normalize_analysis_context_pack_language(report_language) == "en"
        else BLOCK_LABELS_ZH
    )


def iter_analysis_context_pack_block_keys(blocks: Mapping[str, Any]) -> List[str]:
    """按预定义顺序返回 blocks 中出现过的键，未知键追加在尾部。"""
    # 先按 BLOCK_LABELS_ZH 的固定顺序保证一致展示，再兜底补充未列出键。
    ordered_keys = [key for key in BLOCK_LABELS_ZH if key in blocks]
    ordered_keys.extend(key for key in blocks if key not in ordered_keys)
    return ordered_keys


def format_analysis_context_pack_prompt_section(
    pack: Any,
    *,
    report_language: str = "zh",
) -> str:
    """为 AnalysisContextPack 生成低敏的 Prompt 摘要段。

    渲染器刻意忽略 item 内部的具体数值。P3 只把 pack 作为运行期 Prompt
    信号使用；P4 通过独立的低敏概览结构暴露，而不是用本段 prompt 字符串
    或完整 pack 内容。
    """
    payload = _pack_to_dict(pack)
    if not payload:
        return ""

    subject = payload.get("subject")
    blocks = payload.get("blocks")
    if not isinstance(subject, Mapping) or not isinstance(blocks, Mapping):
        return ""

    lang = normalize_analysis_context_pack_language(report_language)
    return _format_en(payload) if lang == "en" else _format_zh(payload)


def analysis_context_pack_to_dict(pack: Any) -> Dict[str, Any]:
    """将 pack 转成 dict：Mapping 直接复制，Pydantic 模型通过 model_dump。"""
    if pack is None:
        return {}
    if isinstance(pack, Mapping):
        return dict(pack)
    model_dump = getattr(pack, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            # 旧版 Pydantic 不支持 mode 参数时退回默认调用。
            dumped = model_dump()
        except Exception:
            # model_dump 自身抛错视为无法序列化，返回空 dict。
            return {}
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


# 保留旧名字作为内部别名（部分模块早期代码依赖此名）。
_pack_to_dict = analysis_context_pack_to_dict


def _format_zh(payload: Dict[str, Any]) -> str:
    """将包内容按中文格式渲染为 Prompt 摘要文本。"""
    lines = ["", "## 分析上下文包摘要"]
    lines.extend(_subject_lines(payload, lang="zh"))
    block_lines = _block_lines(payload, lang="zh")
    if block_lines:
        lines.append("- 数据块状态：")
        lines.extend(f"  - {line}" for line in block_lines)
    metadata_lines = _metadata_lines(payload, lang="zh")
    if metadata_lines:
        lines.extend(metadata_lines)
    warnings = _list_strings(_nested(payload, "data_quality", "warnings"))
    if warnings:
        lines.append(f"- 数据质量提醒：{_join_text(warnings, lang='zh')}")
    lines.extend(_data_limitation_lines(payload, lang="zh"))
    return "\n".join(lines) + "\n"


def _format_en(payload: Dict[str, Any]) -> str:
    """将包内容按英文格式渲染为 Prompt 摘要文本。"""
    lines = ["", "## Analysis Context Pack Summary"]
    lines.extend(_subject_lines(payload, lang="en"))
    block_lines = _block_lines(payload, lang="en")
    if block_lines:
        lines.append("- Data block status:")
        lines.extend(f"  - {line}" for line in block_lines)
    metadata_lines = _metadata_lines(payload, lang="en")
    if metadata_lines:
        lines.extend(metadata_lines)
    warnings = _list_strings(_nested(payload, "data_quality", "warnings"))
    if warnings:
        lines.append(f"- Data quality notes: {_join_text(warnings, lang='en')}")
    lines.extend(_data_limitation_lines(payload, lang="en"))
    return "\n".join(lines) + "\n"


def _subject_lines(payload: Dict[str, Any], *, lang: str) -> List[str]:
    """生成标的代码/名称/市场/版本等主体行（按语言区分分隔符与标点）。"""
    subject = payload.get("subject") if isinstance(payload.get("subject"), Mapping) else {}
    code = _safe_text(subject.get("code"))
    name = _safe_text(subject.get("stock_name"))
    market = _safe_text(subject.get("market"))
    version = _safe_text(payload.get("pack_version"))

    if lang == "en":
        label = code or "unknown"
        if name:
            label += f" ({name})"
        line = f"- Subject: {label}"
        details = []
        if market:
            details.append(f"market={market}")
        if version:
            details.append(f"pack_version={version}")
        if details:
            line += f"; {', '.join(details)}"
        return [line]

    label = code or "未知标的"
    if name:
        label += f"（{name}）"
    line = f"- 标的：{label}"
    details = []
    if market:
        details.append(f"市场={market}")
    if version:
        details.append(f"pack_version={version}")
    if details:
        line += f"；{'，'.join(details)}"
    return [line]


def _block_lines(payload: Dict[str, Any], *, lang: str) -> List[str]:
    """逐 block 生成状态行，按语言选择连接符（中分号 / 英分号）。"""
    blocks = payload.get("blocks")
    if not isinstance(blocks, Mapping):
        return []

    labels = get_analysis_context_pack_block_labels(lang)
    ordered_keys = iter_analysis_context_pack_block_keys(blocks)

    lines: List[str] = []
    for key in ordered_keys:
        block = blocks.get(key)
        if not isinstance(block, Mapping):
            continue
        status = _safe_text(block.get("status")) or "unknown"
        label = labels.get(key, _safe_text(key))
        parts = [f"{label}: {status}"]

        # block 自身 source 缺失时回退到首个 item 的 source。
        source = _first_non_empty(
            block.get("source"),
            _first_item_field(block.get("items"), "source"),
        )
        if source:
            parts.append(f"source={source}")

        warnings = _list_strings(block.get("warnings"))
        if warnings:
            warning_label = "warnings" if lang == "en" else "告警"
            parts.append(f"{warning_label}={_join_text(warnings, lang=lang)}")

        reasons = _item_missing_reasons(block.get("items"))
        if reasons:
            reason_label = "missing_reason" if lang == "en" else "missing_reason"
            parts.append(f"{reason_label}={_join_text(reasons, lang=lang)}")

        lines.append("；".join(parts) if lang == "zh" else "; ".join(parts))
    return lines


def _metadata_lines(payload: Dict[str, Any], *, lang: str) -> List[str]:
    """生成元数据行，目前只覆盖新闻结果数；缺失时返回空列表。"""
    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return []
    news_count = metadata.get("news_result_count")
    if news_count is None:
        return []
    return [
        f"- News result count: {news_count}"
        if lang == "en"
        else f"- 新闻结果数：{news_count}"
    ]


def _data_limitation_lines(payload: Dict[str, Any], *, lang: str) -> List[str]:
    """生成「数据限制」章节，包含评分、限制项、阶段规则与置信度/安全规则。"""
    lines = ["", "## Data Limitations" if lang == "en" else "## 数据限制"]
    data_quality = payload.get("data_quality")
    if not isinstance(data_quality, Mapping):
        data_quality = {}

    score = _safe_score(data_quality.get("overall_score"))
    level = _safe_text(data_quality.get("level"))
    if score is not None:
        level_text = _quality_level_label(level, lang=lang)
        if lang == "en":
            line = f"- Data quality score: {score}/100"
            if level_text:
                line += f" ({level_text})"
        else:
            line = f"- 数据质量评分：{score}/100"
            if level_text:
                line += f"（{level_text}）"
        lines.append(line)

    # limitations 形如 "quote: stale"，替换为本地化的「数据块: 状态」形式。
    limitations = _localized_limitations(
        _list_strings(data_quality.get("limitations")),
        lang=lang,
    )
    if limitations:
        label = "Known limitations" if lang == "en" else "已知限制"
        separator = ": " if lang == "en" else "："
        lines.append(f"- {label}{separator}{_join_text(limitations, lang=lang)}")

    lines.extend(_phase_data_quality_constraint_lines(payload, lang=lang))

    if _has_core_degraded_block(payload):
        if lang == "en":
            lines.append(
                "- Confidence rule: when quote, daily bars, or technical data is "
                "stale, fallback, missing, fetch_failed, partial, or estimated, "
                "the final JSON confidence_level must not be High."
            )
        else:
            lines.append(
                "- 置信度规则：当 quote、daily_bars 或 technical 为 stale、fallback、missing、"
                "fetch_failed、partial 或 estimated 时，最终 JSON 的 confidence_level 不得为高。"
            )

    if lang == "en":
        lines.append(
            "- Analysis rule: missing auxiliary blocks only limit their matching "
            "analysis sections; do not treat missing data itself as bullish or bearish."
        )
        lines.append(
            "- Safety rule: use only status, source, warnings, and missing_reason "
            "from this summary; do not reproduce raw payloads, news body text, "
            "raw trend values, secrets, tokens, or webhooks."
        )
    else:
        lines.append(
            "- 分析规则：辅助数据块缺失只限制对应分析段落，不要把缺失本身解释为利好或利空。"
        )
        lines.append(
            "- 安全规则：只使用本摘要中的 status、source、warnings 和 missing_reason；"
            "不要复述 raw payload、新闻正文、趋势原始值、secret、token 或 webhook。"
        )
    return lines


def _localized_limitations(limitations: List[str], *, lang: str) -> List[str]:
    """把 "block: status" 形式的限制项翻译为本地化的「数据块：状态」。"""
    labels = get_analysis_context_pack_block_labels(lang)
    status_labels = STATUS_LABELS_EN if lang == "en" else STATUS_LABELS_ZH
    result: List[str] = []
    for item in limitations:
        key, separator, status = item.partition(":")
        if not separator:
            result.append(item)
            continue
        normalized_key = key.strip()
        normalized_status = status.strip()
        label = labels.get(normalized_key, _safe_text(normalized_key))
        status_label = status_labels.get(normalized_status, _safe_text(normalized_status))
        if not label or not status_label:
            continue
        result.append(
            f"{label}: {status_label}" if lang == "en" else f"{label}：{status_label}"
        )
    return result[:5]


def _has_core_degraded_block(payload: Dict[str, Any]) -> bool:
    """判断核心数据块（行情/日线/技术）是否存在降级状态。"""
    blocks = payload.get("blocks")
    if not isinstance(blocks, Mapping):
        return False
    for key in ("quote", "daily_bars", "technical"):
        block = blocks.get(key)
        if not isinstance(block, Mapping):
            continue
        status = _safe_text(block.get("status"))
        if status in CORE_DEGRADED_STATUSES:
            return True
    return False


def _phase_data_quality_constraint_lines(payload: Dict[str, Any], *, lang: str) -> List[str]:
    """按市场阶段给出数据质量约束提示（盘中/盘前/非交易等不同口径）。"""
    if not _has_core_degraded_block(payload):
        return []

    phase = _phase_value(payload)
    # 盘后阶段不补充规则：行情数据已稳定，按最终结论输出即可。
    if not phase or phase == "postmarket":
        return []

    if lang == "en":
        if phase in INTRADAY_MARKET_PHASES:
            return [
                "- Phase/data rule: intraday judgment is limited by quote, daily-bar, "
                "or technical data quality; state those limitations before making "
                "near-term trading conclusions."
            ]
        if phase == "premarket":
            return [
                "- Phase/data rule: the opening plan is limited by data freshness "
                "or fallback status; do not describe degraded quote data as "
                "today's completed price action."
            ]
        if phase in CONSERVATIVE_MARKET_PHASES:
            return [
                "- Phase/data rule: use only available data conservatively and do "
                "not fill in nonexistent intraday facts."
            ]
        return []

    if phase in INTRADAY_MARKET_PHASES:
        return [
            "- 阶段数据规则：盘中判断受实时行情、日线或技术数据质量限制；"
            "给出短线结论前必须说明这些限制。"
        ]
    if phase == "premarket":
        return [
            "- 阶段数据规则：开盘计划受数据新鲜度或降级状态限制；"
            "不得把降级行情描述成今日走势已经发生。"
        ]
    if phase in CONSERVATIVE_MARKET_PHASES:
        return [
            "- 阶段数据规则：只能保守使用当前可用数据，不得补全不存在的盘中事实。"
        ]
    return []


def _phase_value(payload: Dict[str, Any]) -> str:
    """读取并校验 pack 中的市场阶段字段，未知值统一返回空字符串。"""
    phase_payload = payload.get("phase")
    if not isinstance(phase_payload, Mapping):
        return ""
    phase = _safe_text(phase_payload.get("phase"))
    return phase if phase in KNOWN_MARKET_PHASES else ""


def _quality_level_label(level: str, *, lang: str) -> str:
    """按语言返回数据质量等级标签，未知等级返回空串。"""
    labels = QUALITY_LEVEL_LABELS_EN if lang == "en" else QUALITY_LEVEL_LABELS_ZH
    return labels.get(level, "")


def _safe_score(value: Any) -> Optional[int]:
    """将 0-100 的整数评分安全转换为 int，非 int 或越界返回 None。"""
    # bool 是 int 的子类但语义不同，需显式排除避免 True/False 被当作 1/0。
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if 0 <= value <= 100:
        return value
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


def _list_strings(value: Any) -> List[str]:
    """把输入清洗为去重且限长（默认 5）的字符串列表。"""
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = _safe_text(item)
        if text and text not in result:
            result.append(text)
    return result[:5]


def _first_non_empty(*values: Any) -> Optional[str]:
    """返回首个非空（经脱敏处理）字符串，全部为空则返回 None。"""
    for value in values:
        text = _safe_text(value)
        if text:
            return text
    return None


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


def _join_text(values: Iterable[str], *, lang: str) -> str:
    """按语言选择分隔符（英文逗号 / 中文顿号）拼接文本。"""
    separator = ", " if lang == "en" else "、"
    return separator.join(values)
