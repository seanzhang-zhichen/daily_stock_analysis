# -*- coding: utf-8 -*-
"""Strategy primitives for stock selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from src.services.stock_selection.models import StockSelectionCandidate, StockSelectionStock


class StockSelectionStrategy(ABC):
    """Base class for pluggable stock selection strategies."""

    name: str
    display_name: str
    description: str
    aliases: tuple[str, ...] = ()

    @abstractmethod
    def default_params(self) -> Dict[str, Any]:
        """Return default strategy parameters."""

    def required_history_days(self, params: Dict[str, Any]) -> int:
        """Return minimum K-line rows needed before evaluating one stock."""
        return int(params.get("lookback_days", 1) or 1)

    def validate_params(self, params: Dict[str, Any]) -> None:
        """Validate strategy-specific parameters."""
        return None

    @abstractmethod
    def evaluate(
        self,
        *,
        stock: StockSelectionStock,
        history: pd.DataFrame,
        source: str,
        params: Dict[str, Any],
        target_date: Optional[date] = None,
    ) -> Optional[StockSelectionCandidate]:
        """Return a candidate when ``stock`` matches the strategy."""


@dataclass(frozen=True)
class StockSelectionStrategyInfo:
    """Public metadata for one selection strategy."""

    name: str
    display_name: str
    description: str
    aliases: List[str]
    default_params: Dict[str, Any]


class StockSelectionStrategyRegistry:
    """Small registry that keeps strategy dispatch explicit and extensible."""

    def __init__(self, strategies: Optional[Iterable[StockSelectionStrategy]] = None) -> None:
        self._strategies: Dict[str, StockSelectionStrategy] = {}
        self._aliases: Dict[str, str] = {}
        for strategy in strategies or []:
            self.register(strategy)

    def register(self, strategy: StockSelectionStrategy) -> None:
        """Register one strategy by name and aliases."""
        key = self._normalize_key(strategy.name)
        if key in self._strategies:
            raise ValueError(f"Duplicate stock selection strategy name: {strategy.name!r}")
        self._strategies[key] = strategy
        self._aliases[key] = key
        for alias in strategy.aliases:
            alias_key = self._normalize_key(alias)
            if not alias_key:
                continue
            existing = self._aliases.get(alias_key)
            if existing and existing != key:
                raise ValueError(f"Duplicate stock selection strategy alias: {alias!r}")
            self._aliases[alias_key] = key

    def get(self, name: str) -> StockSelectionStrategy:
        """Resolve a strategy name or alias."""
        key = self._normalize_key(name)
        strategy_key = self._aliases.get(key)
        if not strategy_key:
            available = ", ".join(sorted(self._strategies))
            raise ValueError(f"Unsupported stock selection strategy: {name!r}. Available: {available}")
        return self._strategies[strategy_key]

    def list(self) -> List[StockSelectionStrategyInfo]:
        """Return registered strategy metadata."""
        return [
            StockSelectionStrategyInfo(
                name=strategy.name,
                display_name=strategy.display_name,
                description=strategy.description,
                aliases=list(strategy.aliases),
                default_params=strategy.default_params(),
            )
            for strategy in self._strategies.values()
        ]

    @staticmethod
    def _normalize_key(value: str) -> str:
        """Normalize a strategy lookup key."""
        return str(value or "").strip().lower().replace("-", "_")
