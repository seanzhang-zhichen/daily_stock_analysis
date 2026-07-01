# -*- coding: utf-8 -*-
"""Tests for stock selection strategies and service orchestration."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import pandas as pd

from src.services.stock_selection.models import StockSelectionStock
from src.services.stock_selection.new_high import NearNewHighStrategy
from src.services.stock_selection.service import StockSelectionService


def _history(
    *,
    start_close: float = 100.0,
    rows: int = 120,
    high_offset: int = 5,
    latest_close: float = 118.0,
    high_value: float = 120.0,
    wobble: float = 0.0,
) -> pd.DataFrame:
    """Build deterministic OHLC history for selection tests."""
    start = date(2026, 1, 1)
    data = []
    for idx in range(rows):
        close = start_close + idx * (latest_close - start_close) / max(rows - 1, 1)
        if wobble:
            close += wobble if idx % 2 == 0 else -wobble
        high = close * 1.01
        data.append(
            {
                "date": (start + timedelta(days=idx)).isoformat(),
                "open": close * 0.99,
                "high": high,
                "low": close * 0.98,
                "close": close,
                "volume": 1000 + idx,
                "pct_chg": 0.1,
            }
        )
    high_idx = rows - 1 - high_offset
    data[high_idx]["high"] = high_value
    data[-1]["close"] = latest_close
    data[-1]["high"] = max(data[-1]["high"], latest_close)
    return pd.DataFrame(data)


class NearNewHighStrategyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = NearNewHighStrategy()
        self.stock = StockSelectionStock(code="600519", name="贵州茅台", market="CN")

    def test_matches_when_price_is_within_85_percent_and_high_is_recent(self) -> None:
        candidate = self.strategy.evaluate(
            stock=self.stock,
            history=_history(latest_close=108.0, high_value=120.0, high_offset=3),
            source="unit",
            params=self.strategy.default_params(),
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.code, "600519")
        self.assertEqual(candidate.days_since_high, 3)
        self.assertAlmostEqual(candidate.distance_to_high_pct, -10.0, places=2)

    def test_rejects_when_latest_close_is_below_threshold(self) -> None:
        candidate = self.strategy.evaluate(
            stock=self.stock,
            history=_history(latest_close=101.0, high_value=120.0, high_offset=3),
            source="unit",
            params=self.strategy.default_params(),
        )

        self.assertIsNone(candidate)

    def test_rejects_when_rolling_high_is_too_old(self) -> None:
        candidate = self.strategy.evaluate(
            stock=self.stock,
            history=_history(latest_close=118.0, high_value=120.0, high_offset=16),
            source="unit",
            params=self.strategy.default_params(),
        )

        self.assertIsNone(candidate)


class StockSelectionServiceTestCase(unittest.TestCase):
    def test_select_sorts_by_volatility_then_return(self) -> None:
        stocks = [
            StockSelectionStock(code="LOW", name="Low Vol", market="CN"),
            StockSelectionStock(code="HIGH", name="High Vol", market="CN"),
        ]
        histories = {
            "LOW": _history(latest_close=118.0, high_value=120.0, high_offset=2, wobble=0.1),
            "HIGH": _history(latest_close=116.0, high_value=120.0, high_offset=2, wobble=5.0),
        }

        def load_history(code, days, target_date):
            return histories[code], "unit"

        service = StockSelectionService(
            db=object(),
            history_loader=load_history,
            stock_pool_loader=lambda markets: stocks,
        )

        result = service.select(strategy_name="near_new_high", markets=["cn"], limit=10)

        self.assertEqual(result.diagnostics.total, 2)
        self.assertEqual(result.diagnostics.matched, 2)
        self.assertEqual([item.code for item in result.items], ["HIGH", "LOW"])

    def test_explicit_codes_are_deduplicated_and_use_history_loader(self) -> None:
        calls = []

        def load_history(code, days, target_date):
            calls.append((code, days, target_date))
            return _history(latest_close=118.0, high_value=120.0, high_offset=1), "unit"

        service = StockSelectionService(
            db=object(),
            history_loader=load_history,
            stock_pool_loader=lambda markets: [],
        )

        result = service.select(stock_codes=["600519", "600519.SH"], limit=10)

        self.assertEqual(len(result.items), 1)
        self.assertEqual(calls[0][0], "600519")


if __name__ == "__main__":
    unittest.main()
