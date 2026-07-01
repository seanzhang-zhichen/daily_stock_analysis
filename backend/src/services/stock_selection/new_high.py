# -*- coding: utf-8 -*-
"""Near new-high stock selection strategy."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

import pandas as pd

from src.services.stock_selection.models import StockSelectionCandidate, StockSelectionStock
from src.services.stock_selection.strategies import StockSelectionStrategy


class NearNewHighStrategy(StockSelectionStrategy):
    """Select stocks trading near a recent rolling-window high."""

    name = "near_new_high"
    display_name = "近新高策略"
    description = "筛选当前价接近 120 日新高，且新高发生在近 15 个交易日内的股票。"
    aliases = ("new_high", "recent_high", "near_high", "新高", "近新高")

    def default_params(self) -> Dict[str, Any]:
        """Return default parameters for the near-new-high strategy."""
        return {
            "lookback_days": 120,
            "min_high_position": 0.85,
            "recent_high_days": 15,
            "sort_by": "volatility_then_return",
        }

    def validate_params(self, params: Dict[str, Any]) -> None:
        """Validate near-new-high parameters."""
        lookback_days = int(params.get("lookback_days", 120))
        recent_high_days = int(params.get("recent_high_days", 15))
        min_high_position = float(params.get("min_high_position", 0.85))
        if lookback_days < 2 or lookback_days > 500:
            raise ValueError("lookback_days must be between 2 and 500")
        if recent_high_days < 0 or recent_high_days > lookback_days:
            raise ValueError("recent_high_days must be between 0 and lookback_days")
        if min_high_position <= 0 or min_high_position > 1:
            raise ValueError("min_high_position must be in (0, 1]")

    def evaluate(
        self,
        *,
        stock: StockSelectionStock,
        history: pd.DataFrame,
        source: str,
        params: Dict[str, Any],
        target_date: Optional[date] = None,
    ) -> Optional[StockSelectionCandidate]:
        """Return a candidate if the latest bar is close to a recent high."""
        lookback_days = int(params.get("lookback_days", 120))
        min_high_position = float(params.get("min_high_position", 0.85))
        recent_high_days = int(params.get("recent_high_days", 15))

        df = _normalize_history(history)
        if target_date is not None and "date" in df.columns:
            df = df[df["date"].dt.date <= target_date]
        if len(df) < lookback_days:
            return None

        window = df.tail(lookback_days).copy()
        if window.empty:
            return None

        high_idx = window["high"].idxmax()
        high_row = window.loc[high_idx]
        latest = window.iloc[-1]

        latest_close = float(latest["close"])
        window_high = float(high_row["high"])
        if latest_close <= 0 or window_high <= 0:
            return None

        high_position = latest_close / window_high
        days_since_high = int(len(window) - 1 - window.index.get_loc(high_idx))
        if high_position < min_high_position or days_since_high > recent_high_days:
            return None

        first_close = float(window.iloc[0]["close"])
        window_return_pct = (latest_close - first_close) / first_close * 100.0 if first_close > 0 else 0.0
        volatility_pct = _calculate_volatility_pct(window["close"])
        distance_to_high_pct = (latest_close / window_high - 1.0) * 100.0
        score = _calculate_score(
            high_position=high_position,
            days_since_high=days_since_high,
            recent_high_days=recent_high_days,
            volatility_pct=volatility_pct,
            window_return_pct=window_return_pct,
        )

        return StockSelectionCandidate(
            code=stock.code,
            name=stock.name,
            market=stock.market,
            strategy=self.name,
            latest_date=_format_date(latest["date"]),
            latest_close=round(latest_close, 4),
            window_high=round(window_high, 4),
            window_high_date=_format_date(high_row["date"]),
            days_since_high=days_since_high,
            distance_to_high_pct=round(distance_to_high_pct, 4),
            window_return_pct=round(window_return_pct, 4),
            volatility_pct=round(volatility_pct, 4),
            score=round(score, 4),
            source=source,
        )


def _normalize_history(history: pd.DataFrame) -> pd.DataFrame:
    """Normalize required columns for strategy calculation."""
    if history is None or history.empty:
        return pd.DataFrame()

    df = history.copy()
    required = ["date", "high", "close"]
    for column in required:
        if column not in df.columns:
            return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["high"] = pd.to_numeric(df["high"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=required)
    df = df[df["high"] > 0]
    df = df[df["close"] > 0]
    return df.sort_values("date", ascending=True).reset_index(drop=True)


def _calculate_volatility_pct(close: pd.Series) -> float:
    """Calculate annualized volatility from daily close returns."""
    returns = close.pct_change().dropna()
    if returns.empty:
        return 0.0
    volatility = float(returns.std(ddof=0) * (252 ** 0.5) * 100.0)
    return 0.0 if pd.isna(volatility) else volatility


def _calculate_score(
    *,
    high_position: float,
    days_since_high: int,
    recent_high_days: int,
    volatility_pct: float,
    window_return_pct: float,
) -> float:
    """Build a stable ranking score where higher is better."""
    recency_score = 1.0 - min(max(days_since_high, 0), recent_high_days) / max(recent_high_days, 1)
    volatility_component = max(volatility_pct, 0.0) / 100.0
    return_component = window_return_pct / 100.0
    return high_position * 60.0 + recency_score * 20.0 + volatility_component * 12.0 + return_component * 8.0


def _format_date(value: Any) -> str:
    """Format a pandas/date-like value as YYYY-MM-DD."""
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)[:10]
