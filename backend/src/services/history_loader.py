"""DB 优先 K 线历史加载器，供 Agent 工具复用。

主要能力：
- 通过 ContextVar 在多线程间传递"冻结的 target_date"
- 提供 ``load_history_df``：先查本地 DB，未命中再用 ``DataFetcherManager`` 兜底

对应 issue #1066：在 Agent 模式下消除每只股票 45+ 次冗余 HTTP 请求。
"""
from __future__ import annotations

import contextvars
import logging
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Any, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)
# 缓存有效判定所需的最小记录数：防止用"只有几条就当作满足 N 天"的假阳性
_CACHE_MIN_RECORDS = 30

# ---------------------------------------------------------------------------
# 冻结的目标日期（ContextVar）—— 每个股票在流水线入口设置一次，
# Agent 工具线程通过 copy_context().run() 读出后保持一致。
# ---------------------------------------------------------------------------
_frozen_target_date: contextvars.ContextVar[Optional[date]] = contextvars.ContextVar(
    "_frozen_target_date", default=None,
)


def set_frozen_target_date(d: date) -> contextvars.Token:
    """冻结当前 Agent 上下文的历史截止日期，返回 token 以便稍后 reset。"""
    return _frozen_target_date.set(d)


def get_frozen_target_date() -> Optional[date]:
    """返回当前上下文里已冻结的目标日期；若从未设置则返回 None。"""
    return _frozen_target_date.get()


def reset_frozen_target_date(token: contextvars.Token) -> None:
    """在单只股票分析结束后恢复之前冻结的目标日期。"""
    _frozen_target_date.reset(token)


# ---------------------------------------------------------------------------
# 内部 ``DataFetcherManager`` 单例（仅作为网络兜底时使用）
# ---------------------------------------------------------------------------
_fetcher_singleton = None
_fetcher_lock = Lock()


def _get_fetcher_manager():
    """惰性创建 ``DataFetcherManager``，仅在 DB 兜底路径中使用，避免常态化的网络请求。"""
    global _fetcher_singleton
    if _fetcher_singleton is None:
        with _fetcher_lock:
            if _fetcher_singleton is None:
                from data_provider import DataFetcherManager
                _fetcher_singleton = DataFetcherManager()
    return _fetcher_singleton


# ---------------------------------------------------------------------------
# DB 优先历史加载器
# ---------------------------------------------------------------------------
def _history_code_candidates(stock_code: str) -> Tuple[List[str], str]:
    """返回 DB 候选代码列表与规范化代码，用于兼容"带/不带前缀"等多种股票代码形态。"""
    from data_provider.base import canonical_stock_code, normalize_stock_code

    raw_code = str(stock_code or "").strip()
    normalized_code = canonical_stock_code(normalize_stock_code(raw_code))
    candidates: List[str] = []
    for candidate in (canonical_stock_code(raw_code), normalized_code):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates, normalized_code


def _coerce_bar_date(value: Any) -> date:
    """把 ORM 或 DataFrame 中类似日期的值强转成 ``date``，无法解析则返回 ``date.min``。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return date.min
    if hasattr(value, "date"):
        try:
            coerced = value.date()
            return coerced if isinstance(coerced, date) else date.min
        except Exception:
            return date.min
    return date.min


def _bar_date(bar: Any) -> date:
    """从 ORM 对象或类 DataFrame 行对象中读取日 K 线日期。"""
    row_date = _coerce_bar_date(getattr(bar, "date", None))
    if row_date != date.min:
        return row_date
    if hasattr(bar, "to_dict"):
        try:
            return _coerce_bar_date((bar.to_dict() or {}).get("date"))
        except Exception:
            return date.min
    return date.min


def _select_best_bars(db, stock_code: str, start: date, end: date) -> Tuple[Optional[str], list]:
    """在多个候选代码中挑出"最新且最大"的一组缓存 K 线，优先使用规范化代码命中。"""
    candidates, normalized_code = _history_code_candidates(stock_code)
    best_code = None
    best_bars = []
    best_key = None

    for candidate in candidates:
        bars = list(db.get_data_range(candidate, start, end) or [])
        if not bars:
            continue
        latest_date = max(_bar_date(bar) for bar in bars)
        # 三元排序键：最新日期 → 条数 → 是否就是规范化代码
        key = (latest_date, len(bars), candidate == normalized_code)
        if best_key is None or key > best_key:
            best_key = key
            best_code = candidate
            best_bars = bars

    return best_code, best_bars


def load_history_df(
    stock_code: str,
    days: int = 60,
    target_date: Optional[date] = None,
) -> Tuple[Optional[pd.DataFrame], str]:
    """加载 K 线历史：DB 优先，DataFetcherManager 兜底。

    返回 ``(df, source)``：DB 命中时 source 为 ``"db_cache"``，网络兜底时为对应
    provider；两者皆失败时返回 ``(None, "none")``。
    """
    from src.storage import get_db

    # 决定本次请求的有效截止日期：优先用入参，否则读取冻结值，否则取今天
    if target_date is not None:
        end = target_date
    else:
        frozen = get_frozen_target_date()
        end = frozen if frozen else date.today()

    # 按"日历日 × 1.8 + 10"补偿周末与长假，确保能取到足够数量的交易日
    start = end - timedelta(days=int(days * 1.8) + 10)

    # --- 1. DB 查询（先规范化代码，再尝试去前缀） -------------------------
    try:
        db = get_db()
        _code, bars = _select_best_bars(db, stock_code, start, end)
        required_records = max(min(days, _CACHE_MIN_RECORDS), 1)
        latest_date = max((_bar_date(bar) for bar in bars), default=date.min)
        if bars and latest_date >= end and len(bars) >= required_records:
            df = pd.DataFrame([b.to_dict() for b in bars])
            logger.debug(
                "load_history_df(%s): %d bars from DB (requested %d)",
                stock_code, len(df), days,
            )
            return df, "db_cache"
    except Exception as e:
        logger.debug("load_history_df(%s): DB read failed: %s", stock_code, e)

    # --- 2. 通过单例 ``DataFetcherManager`` 走网络兜底 --------------------
    try:
        manager = _get_fetcher_manager()
        df, source = manager.get_daily_data(stock_code, days=days)
        if df is not None and not df.empty:
            return df, source
    except Exception as e:
        logger.warning("load_history_df(%s): DataFetcherManager failed: %s", stock_code, e)

    return None, "none"
