# -*- coding: utf-8 -*-
"""Stock selection API schemas."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StockSelectionStrategyItem(BaseModel):
    """Metadata for one stock selection strategy."""

    name: str
    display_name: str
    description: str
    aliases: List[str] = Field(default_factory=list)
    default_params: Dict[str, Any] = Field(default_factory=dict)


class StockSelectionStrategiesResponse(BaseModel):
    """Available stock selection strategies."""

    items: List[StockSelectionStrategyItem] = Field(default_factory=list)


class StockSelectionRequest(BaseModel):
    """Request body for running a stock selection strategy."""

    strategy: str = Field("near_new_high", description="Selection strategy name or alias.")
    stock_codes: Optional[List[str]] = Field(None, description="Optional explicit stock code universe.")
    markets: List[str] = Field(default_factory=lambda: ["cn"], description="Markets used when stock_codes is omitted.")
    limit: int = Field(50, ge=1, le=500, description="Maximum returned candidates.")
    target_date: Optional[date] = Field(None, description="Optional historical target date.")
    lookback_days: Optional[int] = Field(None, ge=2, le=500, description="Rolling high lookback window.")
    min_high_position: Optional[float] = Field(None, gt=0, le=1, description="Latest close / rolling high threshold.")
    recent_high_days: Optional[int] = Field(None, ge=0, le=500, description="Maximum trading days since rolling high.")
    sort_by: Optional[str] = Field(
        None,
        description="Ranking rule: volatility_then_return, return_then_volatility, or score.",
    )


class StockSelectionCandidateItem(BaseModel):
    """One selected stock candidate."""

    code: str
    name: Optional[str] = None
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


class StockSelectionDiagnosticsItem(BaseModel):
    """Counters for a stock selection run."""

    total: int = 0
    processed: int = 0
    matched: int = 0
    no_data: int = 0
    insufficient_data: int = 0
    errors: int = 0
    skipped_unsupported_market: int = 0


class StockSelectionResponse(BaseModel):
    """Result returned after running stock selection."""

    strategy: str
    params: Dict[str, Any] = Field(default_factory=dict)
    items: List[StockSelectionCandidateItem] = Field(default_factory=list)
    diagnostics: StockSelectionDiagnosticsItem
    generated_at: Optional[str] = None
    target_date: Optional[str] = None
