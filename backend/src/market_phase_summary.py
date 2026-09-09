# -*- coding: utf-8 -*-
"""市场阶段（market phase）上下文的低敏感度对外摘要。

把运行时的 `MarketPhaseContext` 字典投影为对外稳定、可控的字段集合（phase、market、布尔标志等），
并负责文本与 JSON 形态的去敏、阶段桶折叠、对外摘要行渲染。
供通知生成、API 响应组装、复盘统计模块复用。
"""
from __future__ import annotations

import json
from datetime import datetime
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

try:
    from src.core.trading_calendar import MarketPhase, build_market_phase_context, get_market_for_stock
except ImportError:  # 旧版交易日历：保留告警 API 可见性但不做阶段推断。
    from enum import Enum
    from src.core.trading_calendar import get_market_for_stock

    class MarketPhase(str, Enum):
        """市场交易阶段枚举（fallback 定义）：盘前/盘中/午休/临收/盘后/非交易/未知。"""
        PREMARKET = "premarket"
        INTRADAY = "intraday"
        LUNCH_BREAK = "lunch_break"
        CLOSING_AUCTION = "closing_auction"
        POSTMARKET = "postmarket"
        NON_TRADING = "non_trading"
        UNKNOWN = "unknown"

    def build_market_phase_context(*, market=None, current_time=None, trigger_source="system", analysis_intent="auto", analysis_phase="auto"):
        """fallback：交易日历不可用时构造阶段未知的降级上下文。"""
        from datetime import datetime
        return type("MarketPhaseContext", (), {
            "to_dict": lambda self: {
                "market": market,
                "phase": "unknown",
                "market_local_time": (current_time or datetime.now()).isoformat(),
                "session_date": (current_time or datetime.now()).date().isoformat(),
                "effective_daily_bar_date": (current_time or datetime.now()).date().isoformat(),
                "is_trading_day": None,
                "is_market_open_now": None,
                "is_partial_bar": None,
                "minutes_to_open": None,
                "minutes_to_close": None,
                "trigger_source": trigger_source,
                "analysis_intent": analysis_intent,
                "warnings": ["market_phase_unavailable"],
            }
        })()


MARKET_PHASE_SUMMARY_KEY = "market_phase_summary"

_ALLOWED_PHASES = tuple(phase.value for phase in MarketPhase)
_BOOLEAN_KEYS = ("is_trading_day", "is_market_open_now", "is_partial_bar")
_INTEGER_KEYS = ("minutes_to_open", "minutes_to_close")
_TEXT_KEYS = (
    "market",
    "market_local_time",
    "session_date",
    "effective_daily_bar_date",
    "trigger_source",
    "analysis_intent",
)
_SENSITIVE_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "credential",
    "webhook",
)
_INTRADAY_BUCKET_PHASES = {"intraday", "lunch_break", "closing_auction"}
_SUPPORTED_MANUAL_ANALYSIS_PHASES = {"premarket", "intraday", "postmarket"}
_SUPPORTED_ANALYSIS_INTENTS = {"auto", *_SUPPORTED_MANUAL_ANALYSIS_PHASES}
_PUBLIC_SOURCE_LABELS_ZH = {
    "alert_trigger_market_context": "告警触发上下文",
    "analysis_history_snapshot": "最近分析快照",
    "evaluator_snapshot": "评估器快照",
    "legacy_text": "历史文本",
}
_PUBLIC_SOURCE_LABELS_EN = {
    "alert_trigger_market_context": "alert trigger context",
    "analysis_history_snapshot": "recent analysis snapshot",
    "evaluator_snapshot": "evaluator snapshot",
    "legacy_text": "legacy text",
}
_MARKET_STATUS_PREFIX = {
    "zh": "市场状态",
    "en": "Market status",
}
_MARKET_LABELS_ZH = {
    "cn": "A股",
    "hk": "港股",
    "us": "美股",
    "tw": "台股",
}
_MARKET_LABELS_EN = {
    "cn": "A-shares",
    "hk": "Hong Kong",
    "us": "US",
    "tw": "Taiwan",
}
_PHASE_LABELS_ZH = {
    "premarket": "盘前",
    "intraday": "盘中",
    "lunch_break": "午间休市",
    "closing_auction": "临近收盘",
    "postmarket": "盘后",
    "non_trading": "非交易日",
    "unknown": "阶段未知",
}
_PHASE_LABELS_EN = {
    "premarket": "Pre-market",
    "intraday": "Intraday",
    "lunch_break": "Lunch break",
    "closing_auction": "Near close",
    "postmarket": "Post-market",
    "non_trading": "Non-trading",
    "unknown": "Unknown phase",
}


def render_market_phase_summary(phase_context: Any) -> Optional[Dict[str, Any]]:
    """把运行时的 `MarketPhaseContext` 字典投影为对外稳定的摘要。

    Args:
        phase_context: dict 形态的阶段上下文，字符串 JSON 也会被自动解析。

    Returns:
        标准化后的摘要字典；阶段非法或输入为空时返回 None。
    """
    payload = _as_mapping(phase_context)
    if not payload:
        return None

    phase = _safe_phase(payload.get("phase"))
    if phase is None:
        return None

    summary: Dict[str, Any] = {"phase": phase}
    for key in _TEXT_KEYS:
        summary[key] = _safe_text(payload.get(key)) or None
    for key in _BOOLEAN_KEYS:
        summary[key] = payload.get(key) if isinstance(payload.get(key), bool) else None
    for key in _INTEGER_KEYS:
        summary[key] = _safe_int(payload.get(key))
    summary["warnings"] = _list_strings(payload.get("warnings"))
    return summary


def extract_market_phase_summary(context_snapshot: Any) -> Optional[Dict[str, Any]]:
    """从持久化快照中提取并重新去敏市场阶段摘要。"""
    snapshot = _as_mapping(context_snapshot)
    if not snapshot:
        return None
    summary = snapshot.get(MARKET_PHASE_SUMMARY_KEY)
    if not isinstance(summary, Mapping):
        return None
    return render_market_phase_summary(summary)


def _parse_phase_local_time(value: Any) -> Optional[datetime]:
    """解析阶段本地时间，支持 datetime 或 ISO 字符串，失败返回 None。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def rebuild_market_phase_summary_for_stock_code(
    stock_code: Any,
    context_snapshot: Any,
) -> Optional[Dict[str, Any]]:
    """为 JP/KR 显示代码重建带派生字段的阶段摘要。

     JP/KR 股票记录上的旧版 CN 快照可能保留 CN 本地值。此辅助函数会基于
     目标市场上下文重新计算派生字段，同时尽量保留非派生来源字段。
    """
    summary = extract_market_phase_summary(context_snapshot)
    if not isinstance(summary, Mapping):
        return None

    market = get_market_for_stock(str(stock_code or "").strip())
    if market not in {"jp", "kr", "tw"}:
        return dict(summary)

    phase = str(summary.get("phase", "")).strip()
    analysis_phase = phase if phase in _SUPPORTED_MANUAL_ANALYSIS_PHASES else "auto"
    analysis_intent = str(summary.get("analysis_intent") or "auto").strip()
    if analysis_intent not in _SUPPORTED_ANALYSIS_INTENTS:
        analysis_intent = "auto"

    rebuilt = build_market_phase_context(
        market=market,
        current_time=_parse_phase_local_time(summary.get("market_local_time")),
        trigger_source=str(summary.get("trigger_source") or "system").strip() or "system",
        analysis_intent=analysis_intent,
        analysis_phase=analysis_phase,
    ).to_dict()

    rebuilt.setdefault("warnings", list(summary.get("warnings") or []))
    if not rebuilt.get("warnings"):
        rebuilt["warnings"] = list(summary.get("warnings") or [])

    return rebuilt


def normalize_analysis_phase_bucket(value: Any) -> str:
    """把详细阶段标签折叠为复盘/统计使用的对外桶。"""
    phase = _safe_text(value)
    if phase == "premarket":
        return "premarket"
    if phase in _INTRADAY_BUCKET_PHASES:
        return "intraday"
    if phase == "postmarket":
        return "postmarket"
    return "unknown"


def format_public_phase_pack_excerpt(
    market_phase_summary: Any,
    analysis_context_pack_overview: Any = None,
    *,
    source: Optional[str] = None,
    report_language: str = "zh",
) -> str:
    """渲染用于通知的低敏感度阶段 + 上下文包摘要片段。"""
    phase_summary = _as_mapping(market_phase_summary)
    overview = _as_mapping(analysis_context_pack_overview)
    if not phase_summary and not overview:
        return ""
    # Korean reuses the English structural summary; output language is set by directive.
    lang = "en" if str(report_language or "").lower().startswith(("en", "ko")) else "zh"
    source_label = _source_label(source, lang)

    lines: List[str] = []
    if phase_summary:
        phase = _safe_text(phase_summary.get("phase")) or "unknown"
        market = _safe_text(phase_summary.get("market"))
        trigger_source = _safe_text(phase_summary.get("trigger_source"))
        if lang == "en":
            parts = [f"phase: {phase}"]
            if market:
                parts.append(f"market: {market}")
            if trigger_source:
                parts.append(f"trigger: {trigger_source}")
            if source_label:
                parts.append(f"source: {source_label}")
            lines.append("- " + " | ".join(parts))
            if phase_summary.get("is_partial_bar") is True:
                lines.append("- partial-bar warning: intraday data may be incomplete")
        else:
            parts = [f"阶段：{phase}"]
            if market:
                parts.append(f"市场：{market}")
            if trigger_source:
                parts.append(f"触发来源：{trigger_source}")
            if source_label:
                parts.append(f"摘要来源：{source_label}")
            lines.append("- " + " | ".join(parts))
            if phase_summary.get("is_partial_bar") is True:
                lines.append("- 盘中数据提示：当前 K 线可能未完结")

    quality = overview.get("data_quality") if isinstance(overview, Mapping) else None
    if isinstance(quality, Mapping):
        level = _safe_text(quality.get("level"))
        if level:
            lines.append(f"- {'data quality' if lang == 'en' else '数据质量'}: {level}")
        limitations = _list_strings(quality.get("limitations"), limit=2)
        for item in limitations:
            lines.append(f"- {'limitation' if lang == 'en' else '限制'}: {item}")

    return "\n".join(lines)


def format_public_market_status_line(
    market_phase_summary: Any,
    *,
    report_language: str = "zh",
) -> str:
    """格式化一行精简的市场 + 阶段摘要，用于聚合报告。"""
    phase_summary = _as_mapping(market_phase_summary)
    if not phase_summary:
        return ""
    phase = _safe_phase(phase_summary.get("phase"))
    if phase is None:
        return ""

    # Korean reuses the English structural summary; output language is set by directive.
    lang = "en" if str(report_language or "").lower().startswith(("en", "ko")) else "zh"
    phase_labels = _PHASE_LABELS_EN if lang == "en" else _PHASE_LABELS_ZH
    market_labels = _MARKET_LABELS_EN if lang == "en" else _MARKET_LABELS_ZH
    phase_label = phase_labels.get(phase, phase)
    market = _safe_text(phase_summary.get("market"))
    market_key = market.lower()
    if market_key:
        market_label = market_labels.get(market_key, market.upper() if lang == "en" else market)
        value = f"{market_label} · {phase_label}"
    else:
        value = phase_label
    separator = ": " if lang == "en" else "："
    return f"{_MARKET_STATUS_PREFIX[lang]}{separator}{value}"


def _as_mapping(value: Any) -> Optional[Mapping[str, Any]]:
    """把 dict 或 JSON 字符串归一化为 Mapping；无法解析时返回 None。"""
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _safe_phase(value: Any) -> Optional[str]:
    """仅返回白名单内的阶段值（_ALLOWED_PHASES），其余视为非法返回 None。"""
    text = _safe_text(value)
    return text if text in _ALLOWED_PHASES else None


def _source_label(value: Any, lang: str) -> Optional[str]:
    """返回摘要来源标签的本地化名称，未匹配时回退原值。"""
    source = _safe_text(value)
    if not source:
        return None
    labels = _PUBLIC_SOURCE_LABELS_EN if lang == "en" else _PUBLIC_SOURCE_LABELS_ZH
    return labels.get(source, source)


def _safe_text(value: Any) -> str:
    """把值安全转为纯文本，并对敏感标记字段做脱敏（返回 [REDACTED]）。"""
    if value is None:
        return ""
    if isinstance(value, (Mapping, list, tuple, set)):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return "[REDACTED]"
    return text


def _safe_int(value: Any) -> Optional[int]:
    """仅接受 int（显式排除 bool），其它类型返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _list_strings(value: Any, *, limit: int = 5) -> List[str]:
    """将列表转为去重非空字符串列表，最多保留 limit 条。"""
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = _safe_text(item)
        if text and text not in result:
            result.append(text)
    return result[:limit]
