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
    """单个选股策略的元数据模型。

    该类用于描述一个选股策略的基本信息，包括名称、显示名、描述、
    别名和默认参数。前端可据此展示策略列表和配置界面。

    Attributes:
        name: 策略内部名称标识，用于唯一识别该策略
        display_name: 策略显示名称，用于前端展示
        description: 策略描述，说明策略的选股逻辑和适用场景
        aliases: 策略别名列表，用于支持多种命名方式调用同一策略
        default_params: 策略默认参数字典，包含各参数的默认值
    """

    name: str
    display_name: str
    description: str
    aliases: List[str] = Field(default_factory=list)
    default_params: Dict[str, Any] = Field(default_factory=dict)


class StockSelectionStrategiesResponse(BaseModel):
    """当前已注册的选股策略清单响应模型。

    该类用于封装查询所有可用选股策略的返回结果。

    Attributes:
        items: 选股策略列表，每个策略为一个 StockSelectionStrategyItem
    """

    items: List[StockSelectionStrategyItem] = Field(default_factory=list)


class StockSelectionRequest(BaseModel):
    """运行一次选股策略的请求载荷模型。

    该类用于封装执行选股策略时的请求参数，支持指定策略名称、
    股票池、市场范围和各类筛选条件。

    Attributes:
        strategy: 选股策略名称或别名，默认为 "near_new_high"（接近新高策略）
        stock_codes: 可选的显式股票代码列表，提供时将只在该列表中选股
        markets: 市场列表，当 stock_codes 未提供时用于确定选股范围，默认为 ["cn"]
        limit: 返回候选标的的最大数量，范围 1-500，默认为 50
        target_date: 可选的历史目标日期，用于回测历史数据
        lookback_days: 滚动高点回顾窗口天数，范围 2-500
        min_high_position: 最新收盘价与滚动高点的比值阈值，范围 0-1
        recent_high_days: 距滚动高点的最大交易日数，范围 0-500
        sort_by: 排序规则，可选值包括：
            - "volatility_then_return": 先按波动率排序，再按收益率排序
            - "return_then_volatility": 先按收益率排序，再按波动率排序
            - "score": 按综合评分排序
    """

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
    """被选中的单只股票候选模型。

    该类用于表示选股结果中的单只候选股票，包含代码、名称、
    市场、价格和各项评分指标。

    Attributes:
        code: 股票代码
        name: 股票名称
        market: 市场标识
        strategy: 选股策略名称
        latest_date: 最新数据日期（ISO 格式）
        latest_close: 最新收盘价
        window_high: 回顾窗口内的最高价
        window_high_date: 最高价出现日期（ISO 格式）
        days_since_high: 距最高价的交易日数
        distance_to_high_pct: 当前价与最高价的距离百分比
        window_return_pct: 回顾窗口内收益率百分比
        volatility_pct: 波动率百分比
        score: 综合评分
        source: 数据来源
    """

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
    """单次选股运行的过程诊断计数器模型。

    该类用于记录选股过程中的各类统计信息，帮助排查问题和优化策略。

    Attributes:
        total: 待处理的股票总数
        processed: 成功处理的股票数量
        matched: 符合选股条件的股票数量
        no_data: 无数据的股票数量
        insufficient_data: 数据不足的股票数量
        errors: 处理过程中发生错误的数量
        skipped_unsupported_market: 因市场不支持而跳过的股票数量
    """

    total: int = 0
    processed: int = 0
    matched: int = 0
    no_data: int = 0
    insufficient_data: int = 0
    errors: int = 0
    skipped_unsupported_market: int = 0


class StockSelectionResponse(BaseModel):
    """运行选股后的完整结果响应模型。

    该类用于封装选股策略执行后的完整返回结果，包含策略信息、
    候选列表、诊断信息和运行时间。

    Attributes:
        strategy: 使用的选股策略名称
        params: 实际使用的选股参数字典
        items: 选中的候选股票列表
        diagnostics: 选股过程诊断信息
        generated_at: 结果生成时间（ISO 格式）
        target_date: 目标日期（ISO 格式），用于回测场景
    """

    strategy: str
    params: Dict[str, Any] = Field(default_factory=dict)
    items: List[StockSelectionCandidateItem] = Field(default_factory=list)
    diagnostics: StockSelectionDiagnosticsItem
    generated_at: Optional[str] = None
    target_date: Optional[str] = None
