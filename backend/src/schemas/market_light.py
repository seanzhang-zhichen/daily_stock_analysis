# -*- coding: utf-8 -*-
"""结构化「市场红绿灯（Market Light）」快照 schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


MarketRegion = Literal["cn", "hk", "us", "jp", "kr"]
MarketLightStatus = Literal["green", "yellow", "red"]
MarketLightDataQuality = Literal["ok", "partial", "unavailable"]
MARKET_LIGHT_REGIONS = frozenset(("cn", "hk", "us"))


class MarketLightDimension(BaseModel):
    """单个 Market Light 评分维度。"""

    score: int = Field(ge=0, le=100)
    available: bool


class MarketLightDimensions(BaseModel):
    """规范的 Market Light 维度评分。"""

    breadth: MarketLightDimension
    index: MarketLightDimension
    limit: MarketLightDimension


class MarketLightSnapshot(BaseModel):
    """结构化的 Market Light 快照，被持久化并供告警消费。"""

    region: MarketRegion
    trade_date: str
    status: MarketLightStatus
    score: int = Field(ge=0, le=100)
    label: str
    temperature_label: str
    reasons: list[str]
    guidance: str
    dimensions: MarketLightDimensions
    data_quality: MarketLightDataQuality
