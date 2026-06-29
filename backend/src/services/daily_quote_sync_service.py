# -*- coding: utf-8 -*-
"""Full-market daily quote synchronization service."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Sequence

from src.config import Config, get_config
from src.core.trading_calendar import get_effective_trading_date, is_market_open

logger = logging.getLogger(__name__)

SUPPORTED_DAILY_QUOTE_SYNC_MARKETS = {"cn"}
_RESOURCE_PATH = Path(__file__).resolve().parents[1] / "data" / "resources" / "stocks.index.json"


@dataclass(frozen=True)
class DailyQuoteSyncStock:
    """One stock selected for daily quote sync."""

    code: str
    market: str = "cn"


@dataclass
class DailyQuoteSyncStats:
    """Summary of one daily quote sync run."""

    target_date: str
    markets: List[str]
    total: int = 0
    skipped_existing: int = 0
    fetched: int = 0
    saved_rows: int = 0
    missing_target_date: int = 0
    failed: int = 0
    skipped_non_trading_day: bool = False

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation."""
        return asdict(self)


class DailyQuoteSyncService:
    """Synchronize full-market daily OHLCV into ``stock_daily``.

    The first implementation intentionally supports CN A-shares only. It uses
    the generated stock index as the stock pool and writes through the existing
    ``save_daily_data`` upsert path, so no extra schema is required.
    """

    def __init__(
        self,
        *,
        config: Optional[Config] = None,
        db=None,
        fetcher_manager: Optional[Any] = None,
        stock_pool_loader: Optional[Callable[[Sequence[str]], List[DailyQuoteSyncStock]]] = None,
    ) -> None:
        self.config = config or get_config()
        if db is None:
            from src.storage import get_db

            db = get_db()
        self.db = db
        if fetcher_manager is None:
            from data_provider import DataFetcherManager

            fetcher_manager = DataFetcherManager()
        self.fetcher_manager = fetcher_manager
        self._stock_pool_loader = stock_pool_loader or self.load_stock_pool

    @staticmethod
    def _canonical_cn_code(raw_code: str) -> str:
        """Normalize common A-share index code shapes to six digits."""
        code = str(raw_code or "").strip().upper()
        if "." in code:
            code = code.split(".", 1)[0]
        for prefix in ("SH", "SZ", "BJ"):
            if code.startswith(prefix):
                code = code[len(prefix) :]
                break
        return code

    @staticmethod
    def _normalize_markets(markets: Optional[Iterable[str]]) -> List[str]:
        """Normalize requested markets and keep supported values only."""
        raw_markets = list(markets or ["cn"])
        normalized: List[str] = []
        unsupported: List[str] = []
        for item in raw_markets:
            market = str(item or "").strip().lower()
            if not market:
                continue
            if market not in SUPPORTED_DAILY_QUOTE_SYNC_MARKETS:
                unsupported.append(market)
                continue
            if market not in normalized:
                normalized.append(market)
        if unsupported:
            logger.warning(
                "每日行情全量同步暂不支持市场: %s；当前仅支持 cn",
                ", ".join(unsupported),
            )
        return normalized or ["cn"]

    @staticmethod
    def _stock_from_index_entry(entry: object, markets: set[str]) -> Optional[DailyQuoteSyncStock]:
        """Convert one stock-index row into a sync stock, if eligible."""
        if isinstance(entry, dict):
            canonical = entry.get("canonicalCode")
            display = entry.get("displayCode")
            market = str(entry.get("market") or "").strip().lower()
            asset_type = str(entry.get("assetType") or "").strip().lower()
            active = bool(entry.get("active", True))
        elif isinstance(entry, (list, tuple)) and len(entry) >= 9:
            canonical, display = entry[0], entry[1]
            market = str(entry[6] or "").strip().lower()
            asset_type = str(entry[7] or "").strip().lower()
            active = bool(entry[8])
        else:
            return None

        if market == "cn":
            market = "cn"
        if market not in markets or asset_type != "stock" or not active:
            return None

        code = DailyQuoteSyncService._canonical_cn_code(str(display or canonical or "").strip())
        if not code or not code.isdigit() or len(code) != 6:
            return None
        return DailyQuoteSyncStock(code=code, market=market)

    @classmethod
    def _load_pool_from_resource(cls, markets: Sequence[str]) -> List[DailyQuoteSyncStock]:
        """Load the generated stock-index JSON resource as a fallback pool."""
        if not _RESOURCE_PATH.exists():
            logger.warning("股票索引资源不存在，无法枚举全量行情同步股票池: %s", _RESOURCE_PATH)
            return []

        with _RESOURCE_PATH.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return cls._stocks_from_entries(payload, markets)

    @classmethod
    def _stocks_from_entries(
        cls,
        entries: Iterable[object],
        markets: Sequence[str],
    ) -> List[DailyQuoteSyncStock]:
        """Build a de-duplicated stock pool from stock-index-like entries."""
        market_set = set(cls._normalize_markets(markets))
        stocks: List[DailyQuoteSyncStock] = []
        seen: set[str] = set()
        for entry in entries:
            stock = cls._stock_from_index_entry(entry, market_set)
            if stock is None or stock.code in seen:
                continue
            stocks.append(stock)
            seen.add(stock.code)
        return stocks

    def load_stock_pool(self, markets: Sequence[str]) -> List[DailyQuoteSyncStock]:
        """Load active stock pool from DB stock index, falling back to resource JSON."""
        try:
            from src.storage import StockIndexEntry

            with self.db.get_session() as session:
                rows = session.query(StockIndexEntry).all()
            stocks = self._stocks_from_entries(
                [
                    {
                        "canonicalCode": row.canonical_code,
                        "displayCode": row.display_code,
                        "market": row.market,
                        "assetType": row.asset_type,
                        "active": row.active,
                    }
                    for row in rows
                ],
                markets,
            )
            if stocks:
                return stocks
        except Exception as exc:
            logger.debug("读取数据库股票索引失败，改用内置资源枚举股票池: %s", exc)

        return self._load_pool_from_resource(markets)

    def _sync_one_stock(
        self,
        stock: DailyQuoteSyncStock,
        *,
        target_date: date,
        lookback_days: int,
    ) -> tuple[str, str, int]:
        """Sync one stock and return ``(status, code, saved_rows)``."""
        if self.db.has_today_data(stock.code, target_date):
            return ("skipped_existing", stock.code, 0)

        df, source_name = self.fetcher_manager.get_daily_data(
            stock.code,
            end_date=target_date.isoformat(),
            days=lookback_days,
        )
        if df is None or df.empty:
            return ("missing_target_date", stock.code, 0)

        saved_rows = int(self.db.save_daily_data(df, stock.code, source_name))
        if self.db.has_today_data(stock.code, target_date):
            return ("fetched", stock.code, saved_rows)
        return ("missing_target_date", stock.code, saved_rows)

    def run(
        self,
        *,
        markets: Optional[Sequence[str]] = None,
        target_date: Optional[date] = None,
        max_workers: Optional[int] = None,
        lookback_days: Optional[int] = None,
        limit: Optional[int] = None,
        force_run: bool = False,
    ) -> DailyQuoteSyncStats:
        """Run full-market daily quote synchronization once."""
        effective_markets = self._normalize_markets(
            markets or getattr(self.config, "daily_quote_sync_markets", ["cn"])
        )
        effective_target_date = target_date or get_effective_trading_date("cn")
        stats = DailyQuoteSyncStats(
            target_date=effective_target_date.isoformat(),
            markets=effective_markets,
        )

        if (
            "cn" in effective_markets
            and not force_run
            and getattr(self.config, "trading_day_check_enabled", True)
            and not is_market_open("cn", effective_target_date)
        ):
            stats.skipped_non_trading_day = True
            logger.info(
                "每日行情全量同步跳过：%s 不是 A 股交易日。可使用 --force-run 强制执行。",
                effective_target_date,
            )
            return stats

        stocks = self._stock_pool_loader(effective_markets)
        if limit is None:
            limit = int(getattr(self.config, "daily_quote_sync_limit", 0) or 0)
        if limit and limit > 0:
            stocks = stocks[:limit]
        stats.total = len(stocks)

        if not stocks:
            logger.warning("每日行情全量同步没有可处理的股票池")
            return stats

        effective_workers = max(
            1,
            int(max_workers or getattr(self.config, "daily_quote_sync_max_workers", 3) or 1),
        )
        effective_lookback_days = max(
            1,
            int(lookback_days or getattr(self.config, "daily_quote_sync_lookback_days", 30) or 30),
        )

        logger.info(
            "开始每日行情全量同步: markets=%s, target_date=%s, stocks=%d, workers=%d, lookback_days=%d",
            ",".join(effective_markets),
            effective_target_date,
            len(stocks),
            effective_workers,
            effective_lookback_days,
        )

        started_at = datetime.now()
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            future_to_stock = {
                executor.submit(
                    self._sync_one_stock,
                    stock,
                    target_date=effective_target_date,
                    lookback_days=effective_lookback_days,
                ): stock
                for stock in stocks
            }
            for future in as_completed(future_to_stock):
                stock = future_to_stock[future]
                try:
                    status, _code, saved_rows = future.result()
                except Exception as exc:
                    stats.failed += 1
                    logger.warning("每日行情同步失败 [%s]: %s", stock.code, exc)
                    continue

                if status == "skipped_existing":
                    stats.skipped_existing += 1
                elif status == "fetched":
                    stats.fetched += 1
                    stats.saved_rows += int(saved_rows or 0)
                elif status == "missing_target_date":
                    stats.missing_target_date += 1
                    stats.saved_rows += int(saved_rows or 0)
                    logger.debug("每日行情同步未获得目标日期数据 [%s]", stock.code)

        elapsed = (datetime.now() - started_at).total_seconds()
        logger.info(
            "每日行情全量同步完成: total=%d fetched=%d skipped=%d missing=%d failed=%d saved_rows=%d elapsed=%.1fs",
            stats.total,
            stats.fetched,
            stats.skipped_existing,
            stats.missing_target_date,
            stats.failed,
            stats.saved_rows,
            elapsed,
        )
        return stats
