# -*- coding: utf-8 -*-
"""Public schemas for the AI decision-signal module."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


DecisionAction = Literal["buy", "add", "hold", "reduce", "sell", "watch", "avoid", "alert"]
DecisionSignalStatus = Literal["active", "expired", "invalidated", "closed", "archived"]


class DecisionSignalItem(BaseModel):
    id: int
    stock_code: str
    stock_name: Optional[str] = None
    market: str
    source_type: str
    source_report_id: int
    trace_id: Optional[str] = None
    trigger_source: str
    action: DecisionAction
    action_label: Optional[str] = None
    confidence: Optional[float] = None
    score: Optional[int] = None
    horizon: Optional[str] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    invalidation: Optional[str] = None
    watch_conditions: Optional[str] = None
    reason: Optional[str] = None
    risk_summary: Optional[str] = None
    catalyst_summary: Optional[str] = None
    evidence: Optional[Any] = None
    data_quality_summary: Optional[Any] = None
    metadata: Optional[Any] = None
    plan_quality: str
    status: DecisionSignalStatus
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DecisionSignalListResponse(BaseModel):
    items: List[DecisionSignalItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class DecisionSignalLatestResponse(BaseModel):
    items: List[DecisionSignalItem] = Field(default_factory=list)


class DecisionSignalStatusUpdateRequest(BaseModel):
    status: DecisionSignalStatus


class DecisionSignalSyncResponse(BaseModel):
    created: int


class DecisionSignalMutationResponse(BaseModel):
    item: DecisionSignalItem


__all__ = [
    "DecisionSignalItem",
    "DecisionSignalLatestResponse",
    "DecisionSignalListResponse",
    "DecisionSignalMutationResponse",
    "DecisionSignalStatusUpdateRequest",
    "DecisionSignalSyncResponse",
]
