# -*- coding: utf-8 -*-
"""选股（Stock Selection）模块的对外 API 契约。

本文件集中描述选股策略元数据、运行请求、候选标的与诊断指标的响应字段，
由 `backend/api/v1/endpoints/stock_selection.py` 在路由层装配后返回给前端。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StockSelectionStrategyItem(BaseModel):
    """单个选股策略的元数据（名称、显示名、描述、别名、默认参数）。"""

    name: str
    display_name: str
    description: str
    aliases: List[str] = Field(default_factory=list)
    default_params: Dict[str, Any] = Field(default_factory=dict)


class StockSelectionStrategiesResponse(BaseModel):
    """当前已注册的选股策略清单响应。"""

    items: List[StockSelectionStrategyItem] = Field(default_factory=list)


class StockSelectionRequest(BaseModel):
    """运行一次选股策略的请求载荷。"""

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
    """被选中的单只股票候选。"""

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
    """单次选股运行的过程诊断计数器。"""

    total: int = 0
    processed: int = 0
    matched: int = 0
    no_data: int = 0
    insufficient_data: int = 0
    errors: int = 0
    skipped_unsupported_market: int = 0


class StockSelectionResponse(BaseModel):
    """运行选股后的完整结果响应。"""

    strategy: str
    params: Dict[str, Any] = Field(default_factory=dict)
    items: List[StockSelectionCandidateItem] = Field(default_factory=list)
    diagnostics: StockSelectionDiagnosticsItem
    generated_at: Optional[str] = None
    target_date: Optional[str] = None
