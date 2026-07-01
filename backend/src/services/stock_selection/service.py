# -*- coding: utf-8 -*-
"""Service layer for running stock selection strategies."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from src.services.history_loader import load_history_df
from src.services.stock_selection.models import (
    StockSelectionCandidate,
    StockSelectionDiagnostics,
    StockSelectionResult,
    StockSelectionStock,
)
from src.services.stock_selection.new_high import NearNewHighStrategy
from src.services.stock_selection.strategies import StockSelectionStrategyInfo, StockSelectionStrategyRegistry

logger = logging.getLogger(__name__)


HistoryLoader = Callable[[str, int, Optional[date]], tuple[Optional[pd.DataFrame], str]]


class StockSelectionService:
    """Run registered stock selection strategies against a stock universe."""

    def __init__(
        self,
        *,
        db: Optional[Any] = None,
        registry: Optional[StockSelectionStrategyRegistry] = None,
        history_loader: Optional[HistoryLoader] = None,
        stock_pool_loader: Optional[Callable[[Sequence[str]], List[StockSelectionStock]]] = None,
    ) -> None:
        if db is None:
            from src.storage import get_db

            db = get_db()
        self.db = db
        self.registry = registry or build_default_registry()
        self.history_loader = history_loader or load_history_df
        self._stock_pool_loader = stock_pool_loader or self.load_stock_pool

    def list_strategies(self) -> List[StockSelectionStrategyInfo]:
        """Return public metadata for available selection strategies."""
        return self.registry.list()

    def select(
        self,
        *,
        strategy_name: str = "near_new_high",
        stock_codes: Optional[Sequence[str]] = None,
        markets: Optional[Sequence[str]] = None,
        limit: int = 50,
        target_date: Optional[date] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> StockSelectionResult:
        """Run one stock selection strategy."""
        strategy = self.registry.get(strategy_name)
        effective_params = strategy.default_params()
        if params:
            effective_params.update({key: value for key, value in params.items() if value is not None})
        strategy.validate_params(effective_params)

        stocks = self._resolve_stock_pool(stock_codes=stock_codes, markets=markets)
        diagnostics = StockSelectionDiagnostics(total=len(stocks))
        items: List[StockSelectionCandidate] = []

        history_days = max(strategy.required_history_days(effective_params), 1)
        request_days = int(effective_params.get("history_days") or history_days)
        request_days = max(request_days, history_days)

        for stock in stocks:
            try:
                df, source = self.history_loader(stock.code, request_days, target_date)
                if df is None or df.empty:
                    diagnostics = replace(diagnostics, no_data=diagnostics.no_data + 1)
                    continue
                if len(df) < history_days:
                    diagnostics = replace(diagnostics, insufficient_data=diagnostics.insufficient_data + 1)
                    continue

                candidate = strategy.evaluate(
                    stock=stock,
                    history=df,
                    source=source,
                    params=effective_params,
                    target_date=target_date,
                )
                diagnostics = replace(diagnostics, processed=diagnostics.processed + 1)
                if candidate is None:
                    continue
                items.append(candidate)
                diagnostics = replace(diagnostics, matched=diagnostics.matched + 1)
            except Exception as exc:  # noqa: BLE001
                diagnostics = replace(diagnostics, errors=diagnostics.errors + 1)
                logger.warning("Stock selection failed for %s via %s: %s", stock.code, strategy.name, exc)

        items = self._sort_candidates(items, str(effective_params.get("sort_by") or "volatility_then_return"))
        max_items = max(1, min(int(limit or 50), 500))
        return StockSelectionResult(
            strategy=strategy.name,
            params=effective_params,
            items=items[:max_items],
            diagnostics=diagnostics,
            generated_at=datetime.now().isoformat(),
            target_date=target_date,
        )

    def _resolve_stock_pool(
        self,
        *,
        stock_codes: Optional[Sequence[str]],
        markets: Optional[Sequence[str]],
    ) -> List[StockSelectionStock]:
        """Resolve explicit stock codes or load a market universe."""
        if stock_codes:
            return self._stocks_from_codes(stock_codes)
        return self._stock_pool_loader(markets or ["cn"])

    def _stocks_from_codes(self, stock_codes: Sequence[str]) -> List[StockSelectionStock]:
        """Build a de-duplicated pool from explicit codes."""
        stocks: List[StockSelectionStock] = []
        seen: set[str] = set()
        for raw_code in stock_codes:
            code = _canonical_stock_code(str(raw_code or "").strip())
            if not code:
                continue
            normalized = _canonical_stock_code(_normalize_stock_code(code))
            dedupe_key = normalized or code
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            stocks.append(
                StockSelectionStock(
                    code=normalized,
                    name=self._get_stock_name(code) or self._get_stock_name(normalized),
                    market=self._infer_market(code),
                )
            )
        return stocks

    def load_stock_pool(self, markets: Sequence[str]) -> List[StockSelectionStock]:
        """Load an active stock universe from DB stock index."""
        normalized_markets = self._normalize_markets(markets)
        rows = self._load_index_rows(normalized_markets)
        stocks: List[StockSelectionStock] = []
        seen: set[str] = set()
        for row in rows:
            code = self._selection_code_from_index_row(row)
            if not code or code in seen:
                continue
            seen.add(code)
            stocks.append(
                StockSelectionStock(
                    code=code,
                    name=str(getattr(row, "name_zh", "") or "") or None,
                    market=str(getattr(row, "market", "") or "CN"),
                )
            )
        return stocks

    def _load_index_rows(self, markets: set[str]) -> Iterable[Any]:
        """Read active stocks from the local stock index."""
        from src.storage import StockIndexEntry

        with self.db.get_session() as session:
            return list(
                session.query(StockIndexEntry)
                .filter(StockIndexEntry.active.is_(True))
                .filter(StockIndexEntry.asset_type == "stock")
                .filter(StockIndexEntry.market.in_({m.upper() for m in markets}))
                .order_by(StockIndexEntry.popularity.desc(), StockIndexEntry.display_code.asc())
                .all()
            )

    def _get_stock_name(self, stock_code: str) -> Optional[str]:
        """Resolve a stock name from stock_index when possible."""
        try:
            from src.repositories.stock_index_repo import StockIndexRepository

            return StockIndexRepository(self.db).get_name_by_code(stock_code)
        except Exception:
            return None

    @staticmethod
    def _selection_code_from_index_row(row: Any) -> str:
        """Choose the code shape expected by data providers and stock_daily."""
        display = str(getattr(row, "display_code", "") or "").strip()
        canonical = str(getattr(row, "canonical_code", "") or "").strip()
        market = str(getattr(row, "market", "") or "").strip().upper()
        if market == "CN":
            return _normalize_stock_code(display or canonical)
        if market == "HK":
            return _normalize_stock_code(canonical or display)
        return _canonical_stock_code(display or canonical)

    @staticmethod
    def _normalize_markets(markets: Sequence[str]) -> set[str]:
        """Normalize market filters."""
        normalized = {str(m or "").strip().lower() for m in markets if str(m or "").strip()}
        return normalized or {"cn"}

    @staticmethod
    def _infer_market(code: str) -> str:
        """Infer a coarse market label from a stock code."""
        normalized = str(code or "").strip().upper()
        if normalized.startswith("HK") or normalized.endswith(".HK"):
            return "HK"
        if normalized.isdigit() or normalized.endswith((".SH", ".SZ", ".SS", ".BJ")):
            return "CN"
        return "US"

    @staticmethod
    def _sort_candidates(
        items: List[StockSelectionCandidate],
        sort_by: str,
    ) -> List[StockSelectionCandidate]:
        """Sort strategy matches by the requested ranking rule."""
        normalized = sort_by.strip().lower()
        if normalized == "return_then_volatility":
            return sorted(
                items,
                key=lambda item: (item.window_return_pct, item.volatility_pct, item.score),
                reverse=True,
            )
        if normalized == "score":
            return sorted(items, key=lambda item: item.score, reverse=True)
        return sorted(
            items,
            key=lambda item: (item.volatility_pct, item.window_return_pct, item.score),
            reverse=True,
        )


def build_default_registry() -> StockSelectionStrategyRegistry:
    """Build the runtime strategy registry."""
    return StockSelectionStrategyRegistry([NearNewHighStrategy()])


def _canonical_stock_code(code: str) -> str:
    """Return an uppercase display/storage code without importing providers."""
    return str(code or "").strip().upper()


def _normalize_stock_code(stock_code: str) -> str:
    """Normalize common CN/HK stock code shapes without provider imports."""
    code = str(stock_code or "").strip()
    upper = code.upper()

    if upper.startswith("HK") and not upper.startswith("HK."):
        candidate = upper[2:]
        if candidate.isdigit() and 1 <= len(candidate) <= 5:
            return f"HK{candidate.zfill(5)}"

    if upper.startswith(("SH", "SZ")) and not upper.startswith(("SH.", "SZ.")):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) in (5, 6):
            return candidate

    if upper.startswith("BJ") and not upper.startswith("BJ."):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) == 6:
            return candidate

    if "." in code:
        base, suffix = code.rsplit(".", 1)
        suffix_upper = suffix.upper()
        if suffix_upper == "HK" and base.isdigit() and 1 <= len(base) <= 5:
            return f"HK{base.zfill(5)}"
        if suffix_upper in {"SH", "SZ", "SS", "BJ"} and base.isdigit():
            return base

    return code
