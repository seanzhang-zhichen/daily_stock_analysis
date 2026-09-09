# -*- coding: utf-8 -*-
"""交易日历辅助函数（覆盖 A股/港股/美股）。

本模块根据股票代码解析所属市场区域，判断市场是否开盘，并计算该市场本地时区下
最新可复用的交易日。``exchange-calendars`` 为可选依赖；当缺失或查询失败时，所有
判断均"放行"（fail-open），避免定时分析被日历依赖问题阻断。
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

# exchange-calendars 是否可用；缺失时关闭严格的日历过滤（退化为 fail-open 放行）。
_XCALS_AVAILABLE = False
try:
    import exchange_calendars as xcals
    _XCALS_AVAILABLE = True
except ImportError:
    logger.warning(
        "exchange-calendars not installed; trading day check disabled. "
        "Run: uv sync --locked"
    )

# 市场 -> exchange-calendars 使用的交易所代码。
MARKET_EXCHANGE = {"cn": "XSHG", "hk": "XHKG", "us": "XNYS"}

# 市场 -> 用于解释"今天"与收盘时间的 IANA 时区。
MARKET_TIMEZONE = {
    "cn": "Asia/Shanghai",
    "hk": "Asia/Hong_Kong",
    "us": "America/New_York",
}

_CLOSING_AUCTION_WINDOW_MINUTES = {"cn": 3}
_SUPPORTED_ANALYSIS_PHASES = {"auto", "premarket", "intraday", "postmarket"}


class MarketPhase(str, Enum):
    """A 股常规交易时段的状态机标签。

    用于大盘复盘与日内分析等场景，标识当前位于盘前、盘中、午休、收盘集合竞价、
    非交易日等哪一阶段。``UNKNOWN`` 表示无法通过日历依赖确定（缺包或查询失败）。
    """

    PREMARKET = "premarket"
    INTRADAY = "intraday"
    LUNCH_BREAK = "lunch_break"
    CLOSING_AUCTION = "closing_auction"
    POSTMARKET = "postmarket"
    NON_TRADING = "non_trading"
    UNKNOWN = "unknown"


@dataclass
class MarketPhaseContext:
    """可在 JSON 中安全序列化、用于分析上下文与提示词注入的市场时段上下文。

    字段同时覆盖交易日判定、当前是否开盘、日线是否仅为部分 bar 等信息，
    以便上层按场景区分对待收盘前后的策略。
    """

    market: Optional[str]
    phase: MarketPhase
    market_local_time: datetime
    session_date: date
    effective_daily_bar_date: date
    is_trading_day: Optional[bool]
    is_market_open_now: Optional[bool]
    is_partial_bar: Optional[bool]
    minutes_to_open: Optional[int] = None
    minutes_to_close: Optional[int] = None
    trigger_source: str = "system"
    analysis_intent: str = "auto"
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """将上下文序列化为可写入 JSON / 注入 prompt 的纯 Python 字典。"""
        return {
            "market": self.market,
            "phase": self.phase.value,
            "market_local_time": self.market_local_time.isoformat(),
            "session_date": self.session_date.isoformat(),
            "effective_daily_bar_date": self.effective_daily_bar_date.isoformat(),
            "is_trading_day": self.is_trading_day,
            "is_market_open_now": self.is_market_open_now,
            "is_partial_bar": self.is_partial_bar,
            "minutes_to_open": self.minutes_to_open,
            "minutes_to_close": self.minutes_to_close,
            "trigger_source": self.trigger_source,
            "analysis_intent": self.analysis_intent,
            "warnings": list(self.warnings),
        }


def get_market_for_stock(code: str) -> Optional[str]:
    """
    根据股票代码推断所属市场区域。

    Returns:
        'cn' | 'hk' | 'us' | None（None 表示无法识别，按 fail-open 视为开盘）
    """
    if not code or not isinstance(code, str):
        return None
    code = (code or "").strip().upper()

    from data_provider import is_us_stock_code, is_us_index_code, is_hk_stock_code

    if is_us_stock_code(code) or is_us_index_code(code):
        return "us"
    if is_hk_stock_code(code):
        return "hk"
    # A股：在美股/港股判定之后再按 6 位纯数字识别，避免指数代码被误判
    if code.isdigit() and len(code) == 6:
        return "cn"
    return None


def is_market_open(market: str, check_date: date) -> bool:
    """
    判断指定市场在给定日期是否开盘。

    Fail-open（放行）：当 exchange-calendars 不可用或日期超出范围时返回 True。

    Args:
        market: 'cn' | 'hk' | 'us'
        check_date: 待检查的日期

    Returns:
        True 表示交易日（或 fail-open），否则为 False
    """
    if not _XCALS_AVAILABLE:
        return True
    ex = MARKET_EXCHANGE.get(market)
    if not ex:
        return True
    try:
        cal = xcals.get_calendar(ex)
        session = datetime(check_date.year, check_date.month, check_date.day)
        return cal.is_session(session)
    except Exception as e:
        logger.warning("trading_calendar.is_market_open fail-open: %s", e)
        return True


def get_market_now(
    market: Optional[str], current_time: Optional[datetime] = None
) -> datetime:
    """
    返回该市场本地时区下的当前时间。

    若传入的 current_time 为无时区（naive）时间，则视为已用市场时区表达。
    未知市场回退为给定的时间（或本地系统时间）。
    """
    tz_name = MARKET_TIMEZONE.get(market or "")

    if current_time is None:
        if tz_name:
            return datetime.now(ZoneInfo(tz_name))
        return datetime.now()

    if not tz_name:
        return current_time

    tz = ZoneInfo(tz_name)
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=tz)
    return current_time.astimezone(tz)


def get_effective_trading_date(
    market: Optional[str], current_time: Optional[datetime] = None
) -> date:
    """
    解析断点续传/检查点逻辑要复用的最新日线日期。

    规则：
    - 非交易日/休市日：取上一交易日
    - 交易日收盘前：取已完成的上一交易时段
    - 交易日收盘后：取当前交易时段
    - 日历查询失败：fail-open 退化为市场本地自然日期
    """
    market_now = get_market_now(market, current_time=current_time)
    fallback_date = market_now.date()

    if not _XCALS_AVAILABLE:
        return fallback_date

    ex = MARKET_EXCHANGE.get(market or "")
    tz_name = MARKET_TIMEZONE.get(market or "")
    if not ex or not tz_name:
        return fallback_date

    try:
        cal = xcals.get_calendar(ex)
        local_date = market_now.date()

        if not cal.is_session(local_date):
            return cal.date_to_session(local_date, direction="previous").date()

        session = cal.date_to_session(local_date, direction="previous")
        session_close = cal.session_close(session)
        # exchange-calendars 返回 pandas Timestamp 或原生 datetime，两条分支都要换算到市场本地时区
        if hasattr(session_close, "tz_convert"):
            close_local = session_close.tz_convert(tz_name).to_pydatetime()
        elif session_close.tzinfo is not None:
            close_local = session_close.astimezone(ZoneInfo(tz_name))
        else:
            close_local = session_close.replace(tzinfo=ZoneInfo(tz_name))

        if market_now >= close_local:
            return session.date()

        return cal.previous_session(session).date()
    except Exception as e:
        logger.warning("trading_calendar.get_effective_trading_date fail-open: %s", e)
        return fallback_date


def _as_market_datetime(value: Any, tz_name: str) -> Optional[datetime]:
    """把 pandas Timestamp / datetime / 其它带 ``to_pydatetime`` 的对象统一转为市场本地时区的 datetime。

    无法识别或解析失败时返回 ``None``，由调用方按 ``UNKNOWN`` 处理。
    """
    if value is None or pd.isna(value):
        return None
    try:
        if isinstance(value, pd.Timestamp):
            dt = value.to_pydatetime() if value.tzinfo is None else value.tz_convert(tz_name).to_pydatetime()
        elif isinstance(value, datetime):
            dt = value
        elif hasattr(value, "to_pydatetime"):
            dt = value.to_pydatetime()
        else:
            return None
    except (AttributeError, TypeError, ValueError):
        return None
    tz = ZoneInfo(tz_name)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def infer_market_phase(
    market: Optional[str], current_time: Optional[datetime] = None
) -> MarketPhase:
    """推断 A 股当前所处的交易时段阶段；日历不可用时退化为 ``UNKNOWN``（fail-closed）。

    判定顺序：非交易日 → 盘前 → 盘后 → 午休/盘中/收盘集合竞价。
    """
    if market != "cn" or not _XCALS_AVAILABLE:
        return MarketPhase.UNKNOWN

    market_now = get_market_now(market, current_time=current_time)
    try:
        cal = xcals.get_calendar(MARKET_EXCHANGE[market])
        if not cal.is_session(market_now.date()):
            return MarketPhase.NON_TRADING
        session = cal.date_to_session(market_now.date(), direction="previous")
        session_open = _as_market_datetime(cal.session_open(session), MARKET_TIMEZONE[market])
        session_close = _as_market_datetime(cal.session_close(session), MARKET_TIMEZONE[market])
        if session_open is None or session_close is None:
            return MarketPhase.UNKNOWN
        if market_now < session_open:
            return MarketPhase.PREMARKET
        if market_now >= session_close:
            return MarketPhase.POSTMARKET

        break_start = _as_market_datetime(cal.session_break_start(session), MARKET_TIMEZONE[market])
        break_end = _as_market_datetime(cal.session_break_end(session), MARKET_TIMEZONE[market])
        closing_start = session_close - timedelta(minutes=_CLOSING_AUCTION_WINDOW_MINUTES[market])
        if break_start is not None and break_end is not None:
            if market_now < break_start:
                return MarketPhase.INTRADAY
            if market_now < break_end:
                return MarketPhase.LUNCH_BREAK
        return MarketPhase.INTRADAY if market_now < closing_start else MarketPhase.CLOSING_AUCTION
    except Exception as exc:
        logger.warning("trading_calendar.infer_market_phase fail-closed: %s", exc)
        return MarketPhase.UNKNOWN


def _phase_booleans(phase: MarketPhase) -> Tuple[Optional[bool], Optional[bool], Optional[bool]]:
    """把 ``MarketPhase`` 翻译成三个布尔：(是否交易日, 是否正在交易, 是否仅为部分 bar)。

    ``UNKNOWN`` 一律返回 ``(None, None, None)``，由调用方决定 fail-open 还是 fail-closed。
    """
    if phase == MarketPhase.UNKNOWN:
        return None, None, None
    return (
        phase != MarketPhase.NON_TRADING,
        phase in {MarketPhase.INTRADAY, MarketPhase.CLOSING_AUCTION},
        phase in {MarketPhase.INTRADAY, MarketPhase.LUNCH_BREAK, MarketPhase.CLOSING_AUCTION},
    )


def _normalize_analysis_phase(analysis_phase: Optional[str], analysis_intent: Optional[str]) -> str:
    """归一化调用方传入的 ``analysis_phase``，并向下兼容旧的 ``analysis_intent`` 字段。

    未知值抛 ``ValueError``，避免错误配置静默生效。
    """
    requested = str(analysis_phase or "auto").strip().lower() or "auto"
    legacy = str(analysis_intent or "").strip().lower()
    if requested == "auto" and legacy and legacy != "auto":
        requested = legacy
    if requested not in _SUPPORTED_ANALYSIS_PHASES:
        raise ValueError(f"invalid analysis_phase: {requested}")
    return requested


def build_market_phase_context(
    *,
    market: Optional[str],
    current_time: Optional[datetime] = None,
    trigger_source: str = "system",
    analysis_intent: str = "auto",
    analysis_phase: str = "auto",
) -> MarketPhaseContext:
    """构造 A 股市场时段上下文，**不改变**现有分析主流程行为。

    当 ``analysis_phase == "auto"`` 时调用 :func:`infer_market_phase`，否则按入参直接封
    装。``warnings`` 字段收集 ``unknown_market`` / ``calendar_unavailable`` /
    ``calendar_error`` 等软错误以便上游决定降级策略。
    """
    requested = _normalize_analysis_phase(analysis_phase, analysis_intent)
    market_now = get_market_now(market, current_time=current_time)
    warnings: List[str] = []
    if market != "cn":
        phase = MarketPhase.UNKNOWN
        warnings.append("unknown_market")
    else:
        if not _XCALS_AVAILABLE:
            warnings.append("calendar_unavailable")
        phase = infer_market_phase(market, current_time) if requested == "auto" else MarketPhase(requested)
        if phase == MarketPhase.UNKNOWN and _XCALS_AVAILABLE:
            warnings.append("calendar_error")

    is_trading_day, is_market_open_now, is_partial_bar = _phase_booleans(phase)
    minutes_to_open = minutes_to_close = None
    if phase == MarketPhase.PREMARKET or phase in {MarketPhase.INTRADAY, MarketPhase.LUNCH_BREAK, MarketPhase.CLOSING_AUCTION}:
        try:
            cal = xcals.get_calendar(MARKET_EXCHANGE["cn"]) if _XCALS_AVAILABLE else None
            if cal is not None and cal.is_session(market_now.date()):
                session = cal.date_to_session(market_now.date(), direction="previous")
                session_open = _as_market_datetime(cal.session_open(session), MARKET_TIMEZONE["cn"])
                session_close = _as_market_datetime(cal.session_close(session), MARKET_TIMEZONE["cn"])
                if phase == MarketPhase.PREMARKET and session_open and market_now < session_open:
                    minutes_to_open = max(0, int((session_open - market_now).total_seconds() // 60))
                elif session_close and market_now < session_close:
                    minutes_to_close = max(0, int((session_close - market_now).total_seconds() // 60))
        except Exception as exc:
            logger.warning("trading_calendar.market_phase_context calendar_error: %s", exc)
            warnings.append("calendar_error")

    return MarketPhaseContext(
        market=market, phase=phase, market_local_time=market_now,
        session_date=market_now.date(),
        effective_daily_bar_date=get_effective_trading_date(market, current_time),
        is_trading_day=is_trading_day, is_market_open_now=is_market_open_now,
        is_partial_bar=is_partial_bar, minutes_to_open=minutes_to_open,
        minutes_to_close=minutes_to_close, trigger_source=trigger_source or "system",
        analysis_intent=requested, warnings=list(dict.fromkeys(warnings)),
    )


def get_open_markets_today() -> Set[str]:
    """
    获取今天开盘的市场（按各市场本地时区判断）。

    Returns:
        今天正在交易的市场键集合（'cn'、'hk'、'us'）
    """
    if not _XCALS_AVAILABLE:
        # 日历不可用时全市场放行，保证大盘复盘不会被单点依赖故障阻塞
        return {"cn", "hk", "us"}
    result: Set[str] = set()
    for mkt, tz_name in MARKET_TIMEZONE.items():
        try:
            tz = ZoneInfo(tz_name)
            today = datetime.now(tz).date()
            if is_market_open(mkt, today):
                result.add(mkt)
        except Exception as e:
            logger.warning("get_open_markets_today fail-open for %s: %s", mkt, e)
            result.add(mkt)
    return result


def compute_effective_region(
    config_region: str, open_markets: Set[str]
) -> Optional[str]:
    """
    结合配置与今日开盘市场，计算有效的大盘复盘区域。

    Args:
        config_region: 来自 MARKET_REVIEW_REGION（'cn' | 'hk' | 'us' | 'both'）
        open_markets: 今天开盘的市场

    Returns:
        None：调用方使用配置默认值（不启用过滤）
        ''：相关市场全部休市，跳过复盘
        'cn' | 'hk' | 'us' | 'both'：今日的有效子集
    """
    if config_region not in ("cn", "hk", "us", "both"):
        config_region = "cn"
    if config_region in ("cn", "hk", "us"):
        return config_region if config_region in open_markets else ""
    # both：只保留今天实际开盘的市场，避免对休市市场做无效复盘
    parts = [m for m in ("cn", "hk", "us") if m in open_markets]
    if not parts:
        return ""
    if len(parts) == 1:
        # 只有一个市场开盘时直接返回市场键，保持与单市场配置相同的取值形态
        return parts[0]
    return ",".join(parts)
