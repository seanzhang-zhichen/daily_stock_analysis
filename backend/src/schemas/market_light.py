# -*- coding: utf-8 -*-
"""结构化「市场红绿灯（Market Light）」快照 schema。

本模块定义了 Market Light 系统的 Pydantic 模型，
用于表示市场整体健康度的多维度评分（绿灯/黄灯/红灯）。
Market Light 综合了市场广度、指数表现、涨跌停限制等多个维度，
为交易决策提供直观的市场状态指示。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


MarketRegion = Literal["cn", "hk", "us", "jp", "kr"]
"""支持的市场区域代码：中国、香港、美国、日本、韩国。"""

MarketLightStatus = Literal["green", "yellow", "red"]
"""市场红绿灯状态：绿灯（健康）、黄灯（谨慎）、红灯（危险）。"""

MarketLightDataQuality = Literal["ok", "partial", "unavailable"]
"""数据质量状态：正常、部分缺失、不可用。"""

MARKET_LIGHT_REGIONS = frozenset(("cn", "hk", "us"))
"""默认启用 Market Light 功能的市场区域集合。"""


class MarketLightDimension(BaseModel):
    """单个 Market Light 评分维度。

    表示市场某个具体维度的得分和可用性，
    如市场广度、指数表现、涨跌停限制等。

    Attributes:
        score: 该维度的得分，范围 0-100
        available: 该维度数据是否可用
    """

    score: int = Field(ge=0, le=100)
    """维度得分，范围 0-100，越高表示该维度越健康。"""

    available: bool
    """该维度数据是否可用；False 表示数据缺失或无法计算。"""


class MarketLightDimensions(BaseModel):
    """规范的 Market Light 维度评分。

    聚合三个核心维度的评分：
    - breadth: 市场广度，反映上涨家数占比
    - index: 指数表现，反映主要指数涨跌
    - limit: 涨跌停限制，反映极端涨跌家数
    """

    breadth: MarketLightDimension
    """市场广度维度：上涨股票数量占比，反映市场整体参与度。"""

    index: MarketLightDimension
    """指数表现维度：主要市场指数的涨跌幅表现。"""

    limit: MarketLightDimension
    """涨跌停限制维度：涨停/跌停家数占比，反映市场情绪极端程度。"""


class MarketLightSnapshot(BaseModel):
    """结构化的 Market Light 快照，被持久化并供告警消费。

    包含某个市场区域在特定交易日的完整 Market Light 评估结果，
    包括总体状态、综合得分、各维度评分、原因分析和操作建议。
    该快照可被持久化到数据库，也可被告警系统消费。

    Attributes:
        region: 市场区域代码
        trade_date: 交易日，格式通常为 YYYY-MM-DD
        status: 总体红绿灯状态
        score: 综合得分 0-100
        label: 状态的人类可读标签
        temperature_label: 市场温度标签（如"火热"、"寒冷"）
        reasons: 评分背后的原因列表
        guidance: 操作建议或指导
        dimensions: 各维度详细评分
        data_quality: 数据质量状态
    """

    region: MarketRegion
    """市场区域代码，如 'cn'、'hk'、'us'。"""

    trade_date: str
    """交易日字符串，格式通常为 YYYY-MM-DD。"""

    status: MarketLightStatus
    """总体红绿灯状态：green（健康）、yellow（谨慎）、red（危险）。"""

    score: int = Field(ge=0, le=100)
    """综合得分，范围 0-100，基于各维度加权计算。"""

    label: str
    """状态的人类可读标签，如"强势"、"震荡"、"弱势"。"""

    temperature_label: str
    """市场温度标签，如"火热"、"温暖"、"寒冷"，用于直观感受市场情绪。"""

    reasons: list[str]
    """评分背后的原因列表，解释为什么给出当前评分。"""

    guidance: str
    """基于当前市场状态的操作建议或指导。"""

    dimensions: MarketLightDimensions
    """各维度的详细评分，包括广度、指数、涨跌停限制。"""

    data_quality: MarketLightDataQuality
    """数据质量状态：ok（正常）、partial（部分缺失）、unavailable（不可用）。"""
