# -*- coding: utf-8 -*-
"""Tests for full-market daily quote synchronization."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import date
from unittest.mock import patch

from src.services.daily_quote_sync_service import (
    DailyQuoteSyncService,
    DailyQuoteSyncStock,
)


@dataclass
class _Config:
    daily_quote_sync_markets: list[str] = field(default_factory=lambda: ["cn"])
    daily_quote_sync_max_workers: int = 1
    daily_quote_sync_lookback_days: int = 30
    daily_quote_sync_limit: int = 0
    trading_day_check_enabled: bool = True


class _FakeDb:
    def __init__(self, existing: set[tuple[str, date]] | None = None):
        self.existing = set(existing or set())
        self.saved: list[tuple[str, str]] = []

    def has_today_data(self, code: str, target_date: date) -> bool:
        return (code, target_date) in self.existing

    def save_daily_data(self, df: pd.DataFrame, code: str, source_name: str) -> int:
        self.saved.append((code, source_name))
        for value in df["date"]:
            self.existing.add((code, value))
        return len(df)


class _FakeFrame:
    def __init__(self, dates: list[date] | None = None):
        self._dates = list(dates or [])

    @property
    def empty(self) -> bool:
        return not self._dates

    def __len__(self) -> int:
        return len(self._dates)

    def __getitem__(self, key: str):
        if key != "date":
            raise KeyError(key)
        return list(self._dates)


class _FakeFetcher:
    def __init__(self, frame_by_code: dict[str, _FakeFrame]):
        self.frame_by_code = frame_by_code
        self.calls: list[tuple[str, str, int]] = []

    def get_daily_data(self, code: str, *, end_date: str, days: int):
        self.calls.append((code, end_date, days))
        return self.frame_by_code.get(code, _FakeFrame()), "FakeFetcher"


class DailyQuoteSyncServiceTestCase(unittest.TestCase):
    def test_stocks_from_entries_filters_active_cn_stock_codes(self):
        entries = [
            ["000001.SZ", "000001", "平安银行", "", "", [], "CN", "stock", True, 100],
            ["00700.HK", "HK00700", "腾讯控股", "", "", [], "HK", "stock", True, 100],
            ["000002.SZ", "000002", "万科A", "", "", [], "CN", "stock", False, 100],
            ["510300.SH", "510300", "沪深300ETF", "", "", [], "CN", "fund", True, 100],
            ["600519.SH", "600519", "贵州茅台", "", "", [], "CN", "stock", True, 100],
        ]

        stocks = DailyQuoteSyncService._stocks_from_entries(entries, ["cn"])

        self.assertEqual([stock.code for stock in stocks], ["000001", "600519"])

    @patch("src.services.daily_quote_sync_service.is_market_open", return_value=True)
    def test_run_skips_existing_and_saves_missing_target_date(self, _mock_market_open):
        target = date(2026, 6, 26)
        df = _FakeFrame([target])
        db = _FakeDb(existing={("000001", target)})
        fetcher = _FakeFetcher({"600519": df})
        service = DailyQuoteSyncService(
            config=_Config(),
            db=db,
            fetcher_manager=fetcher,
            stock_pool_loader=lambda _markets: [
                DailyQuoteSyncStock("000001"),
                DailyQuoteSyncStock("600519"),
            ],
        )

        stats = service.run(markets=["cn"], target_date=target, max_workers=1)

        self.assertEqual(stats.total, 2)
        self.assertEqual(stats.skipped_existing, 1)
        self.assertEqual(stats.fetched, 1)
        self.assertEqual(stats.saved_rows, 1)
        self.assertEqual(stats.failed, 0)
        self.assertEqual(fetcher.calls, [("600519", "2026-06-26", 30)])
        self.assertIn(("600519", target), db.existing)

    @patch("src.services.daily_quote_sync_service.is_market_open", return_value=False)
    def test_run_respects_non_trading_day_check(self, _mock_market_open):
        service = DailyQuoteSyncService(
            config=_Config(),
            db=_FakeDb(),
            fetcher_manager=_FakeFetcher({}),
            stock_pool_loader=lambda _markets: [DailyQuoteSyncStock("600519")],
        )

        stats = service.run(markets=["cn"], target_date=date(2026, 6, 27), force_run=False)

        self.assertTrue(stats.skipped_non_trading_day)
        self.assertEqual(stats.total, 0)

    @patch("src.services.daily_quote_sync_service.is_market_open", return_value=True)
    def test_run_marks_missing_when_provider_lacks_target_date(self, _mock_market_open):
        target = date(2026, 6, 26)
        old_df = _FakeFrame([date(2026, 6, 25)])
        service = DailyQuoteSyncService(
            config=_Config(),
            db=_FakeDb(),
            fetcher_manager=_FakeFetcher({"600519": old_df}),
            stock_pool_loader=lambda _markets: [DailyQuoteSyncStock("600519")],
        )

        stats = service.run(markets=["cn"], target_date=target, max_workers=1)

        self.assertEqual(stats.fetched, 0)
        self.assertEqual(stats.missing_target_date, 1)
        self.assertEqual(stats.saved_rows, 1)


if __name__ == "__main__":
    unittest.main()
