# -*- coding: utf-8 -*-
"""Shared models for stock selection strategies."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class StockSelectionStock:
    """One stock in a selection universe."""

    code: str
    name: Optional[str] = None
    market: str = "CN"


@dataclass(frozen=True)
class StockSelectionCandidate:
    """One stock that matched a selection strategy."""

    code: str
    name: Optional[str]
    market: str
    strategy: str
    latest_date: str
    latest_close: float
    window_high: float
    window_high_date: str
    days_since_high: int
    distance_to_high_pct: float
    window_return_pct: float
    volatility_pct: float
    score: float
    source: str

    def to_dict(self) -> Dict[str, Any]:
        """Return an API-friendly dict."""
        return asdict(self)


@dataclass(frozen=True)
class StockSelectionDiagnostics:
    """Counters describing a selection run."""

    total: int = 0
    processed: int = 0
    matched: int = 0
    no_data: int = 0
    insufficient_data: int = 0
    errors: int = 0
    skipped_unsupported_market: int = 0

    def to_dict(self) -> Dict[str, int]:
        """Return an API-friendly dict."""
        return asdict(self)


@dataclass(frozen=True)
class StockSelectionResult:
    """Result returned by the stock selection service."""

    strategy: str
    params: Dict[str, Any]
    items: List[StockSelectionCandidate] = field(default_factory=list)
    diagnostics: StockSelectionDiagnostics = field(default_factory=StockSelectionDiagnostics)
    generated_at: Optional[str] = None
    target_date: Optional[date] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return an API-friendly dict."""
        return {
            "strategy": self.strategy,
            "params": dict(self.params),
            "items": [item.to_dict() for item in self.items],
            "diagnostics": self.diagnostics.to_dict(),
            "generated_at": self.generated_at,
            "target_date": self.target_date.isoformat() if self.target_date else None,
        }
