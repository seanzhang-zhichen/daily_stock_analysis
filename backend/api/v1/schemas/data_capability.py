# -*- coding: utf-8 -*-
"""数据能力（Data Capability）Schema 定义。

本模块定义数据能力服务对外暴露的 Pydantic 模型，包括数据提供方能力、
数据集能力以及聚合响应。用于前端展示各数据源的可用状态和配置情况。
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class ProviderCapability(BaseModel):
    """单个数据提供方的能力描述模型。

    该类用于描述一个数据提供方的完整能力信息，包括其配置状态、
    运行状态、优先级以及支持的市场和数据集列表。前端可据此展示
    各数据源的可用状态和配置情况。

    Attributes:
        name: 提供方名称标识，用于唯一识别该数据源
        configured: 是否已完成必要配置（如 API Key 已设置、账户已激活等）
        status: 当前运行状态，常见值包括 'ok'（正常）、'degraded'（降级）、
            'unavailable'（不可用）
        priority: 优先级数值，数值越小优先级越高，用于多提供方时的优选策略
        markets: 支持的市场列表，例如 ["cn", "hk", "us"]
        datasets: 支持的数据集列表，例如 ["quote", "kline", "financial"]
    """

    name: str
    """提供方名称标识，用于唯一识别该数据源。"""

    configured: bool
    """是否已完成必要配置（如 API Key 已设置、账户已激活等）。"""

    status: str
    """当前运行状态，常见值包括 'ok'（正常）、'degraded'（降级）、'unavailable'（不可用）。"""

    priority: Optional[int] = None
    """优先级数值，数值越小优先级越高，用于多提供方时的优选策略。"""

    markets: List[str] = Field(default_factory=list)
    """支持的市场列表，例如 ["cn", "hk", "us"]。"""

    datasets: List[str] = Field(default_factory=list)
    """支持的数据集列表，例如 ["quote", "kline", "financial"]。"""


class DatasetCapability(BaseModel):
    """单个数据集的能力描述模型。

    该类用于描述某个数据集在特定市场下的可用状态，以及支持该数据集的
    提供方列表。帮助前端了解某个具体数据集（如行情、财务数据等）
    在不同市场的覆盖情况。

    Attributes:
        dataset: 数据集名称标识，例如 "quote"、"kline"、"financial" 等
        market: 市场标识，例如 'cn'（中国）、'hk'（香港）、'us'（美国）
        status: 可用状态，例如 'ok'（完全可用）、'partial'（部分可用）、
            'unavailable'（不可用）
        providers: 支持该数据集的提供方名称列表
        reason: 状态说明或不可用原因，用于向用户解释当前状态
    """

    dataset: str
    """数据集名称标识，例如 "quote"、"kline"、"financial" 等。"""

    market: str
    """市场标识，例如 'cn'（中国）、'hk'（香港）、'us'（美国）。"""

    status: str
    """可用状态，例如 'ok'（完全可用）、'partial'（部分可用）、'unavailable'（不可用）。"""

    providers: List[str] = Field(default_factory=list)
    """支持该数据集的提供方名称列表。"""

    reason: Optional[str] = None
    """状态说明或不可用原因，用于向用户解释当前状态。"""


class DataCapabilityResponse(BaseModel):
    """数据能力查询的聚合响应模型。

    该类作为数据能力查询接口的统一响应格式，包含查询时间戳、
    提供方能力列表、数据集能力列表和警告信息。前端可据此构建
    完整的数据源状态看板。

    Attributes:
        as_of: 查询时间戳（ISO 格式），表示数据快照的生成时间
        providers: 数据提供方能力列表，包含所有已注册提供方的状态信息
        datasets: 数据集能力列表，包含所有数据集在各市场的可用性信息
        warnings: 警告信息列表，如配置缺失、服务降级等需要注意的事项
    """

    as_of: str
    """查询时间戳（ISO 格式），表示数据快照的生成时间。"""

    providers: List[ProviderCapability] = Field(default_factory=list)
    """数据提供方能力列表，包含所有已注册提供方的状态信息。"""

    datasets: List[DatasetCapability] = Field(default_factory=list)
    """数据集能力列表，包含所有数据集在各市场的可用性信息。"""

    warnings: List[str] = Field(default_factory=list)
    """警告信息列表，如配置缺失、服务降级等需要注意的事项。"""
