# -*- coding: utf-8 -*-
"""Backtest API schemas.

回测接口用于评估历史分析建议在后续行情中的表现。这里的模型描述回测任务触发、
分页结果和聚合指标，字段保持接近存储层结果，方便前端表格和指标卡直接消费。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BacktestRunRequest(BaseModel):
    """触发或刷新回测计算的请求体。

    用于手动触发回测任务，支持指定股票、评估窗口、最小天龄等参数，
    以便灵活控制回测范围和粒度。

    Attributes:
        code: 仅回测指定股票，可选。不指定时回测所有符合条件的股票
        force: 是否强制重新计算，默认 False。True 时会忽略已有结果重新计算
        eval_window_days: 评估窗口（交易日数），可选，范围 1-120 天
        min_age_days: 分析记录最小天龄（0=不限），可选，范围 0-365 天
        limit: 最多处理的分析记录数，默认 200，范围 1-2000
    """

    code: Optional[str] = Field(None, description="仅回测指定股票")
    force: bool = Field(False, description="强制重新计算")
    eval_window_days: Optional[int] = Field(None, ge=1, le=120, description="评估窗口（交易日数）")
    min_age_days: Optional[int] = Field(None, ge=0, le=365, description="分析记录最小天龄（0=不限）")
    limit: int = Field(200, ge=1, le=2000, description="最多处理的分析记录数")


class BacktestRunResponse(BaseModel):
    """回测任务完成后的概要计数响应。

    用于返回回测任务执行后的统计结果，包括处理记录数、
    完成数、数据不足数和错误数等。

    Attributes:
        processed: 候选记录数（被纳入回测范围的分析记录总数）
        saved: 写入回测结果数（成功保存到数据库的回测结果数）
        completed: 完成回测数（成功完成回测计算的记录数）
        insufficient: 数据不足数（因数据不足而无法完成回测的记录数）
        errors: 错误数（回测过程中发生错误的记录数）
    """

    processed: int = Field(..., description="候选记录数")
    saved: int = Field(..., description="写入回测结果数")
    completed: int = Field(..., description="完成回测数")
    insufficient: int = Field(..., description="数据不足数")
    errors: int = Field(..., description="错误数")


class BacktestResultItem(BaseModel):
    """单条历史分析记录的回测结果及对应的实际行情表现。

    该类包含一条分析记录在回测期间的全部表现数据，
    包括分析时的建议、实际行情走势、收益率等关键指标。

    Attributes:
        analysis_history_id: 关联的分析历史记录 ID
        code: 股票代码
        stock_name: 股票名称，可选
        analysis_date: 分析日期（ISO 格式），可选
        eval_window_days: 评估窗口（交易日数）
        engine_version: 回测引擎版本号
        eval_status: 评估状态（如 "completed" / "insufficient_data" 等）
        evaluated_at: 评估时间（ISO 格式），可选
        operation_advice: 分析时的操作建议（如 "买入" / "持有" / "卖出"），可选
        trend_prediction: 趋势预测，可选
        position_recommendation: 仓位建议，可选
        start_price: 评估起始价格，可选
        end_close: 评估期末收盘价，可选
        max_high: 评估期内最高价，可选
        min_low: 评估期内最低价，可选
        stock_return_pct: 股票收益率（百分比），可选
        actual_return_pct: 实际收益率（百分比），可选
        actual_movement: 实际走势描述，可选
        direction_expected: 预期方向（如 "up" / "down"），可选
        direction_correct: 方向判断是否正确，可选
        outcome: 回测结果描述，可选
        stop_loss: 止损价格，可选
        take_profit: 止盈价格，可选
        hit_stop_loss: 是否触及止损，可选
        hit_take_profit: 是否触及止盈，可选
        first_hit: 首次触及的条件（"stop_loss" / "take_profit"），可选
        first_hit_date: 首次触及日期（ISO 格式），可选
        first_hit_trading_days: 首次触及所需交易日数，可选
        simulated_entry_price: 模拟入场价格，可选
        simulated_exit_price: 模拟出场价格，可选
        simulated_exit_reason: 模拟出场原因，可选
        simulated_return_pct: 模拟收益率（百分比），可选
    """

    analysis_history_id: int
    code: str
    stock_name: Optional[str] = None
    analysis_date: Optional[str] = None
    eval_window_days: int
    engine_version: str
    eval_status: str
    evaluated_at: Optional[str] = None
    operation_advice: Optional[str] = None
    trend_prediction: Optional[str] = None
    position_recommendation: Optional[str] = None
    start_price: Optional[float] = None
    end_close: Optional[float] = None
    max_high: Optional[float] = None
    min_low: Optional[float] = None
    stock_return_pct: Optional[float] = None
    actual_return_pct: Optional[float] = None
    actual_movement: Optional[str] = None
    direction_expected: Optional[str] = None
    direction_correct: Optional[bool] = None
    outcome: Optional[str] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    hit_stop_loss: Optional[bool] = None
    hit_take_profit: Optional[bool] = None
    first_hit: Optional[str] = None
    first_hit_date: Optional[str] = None
    first_hit_trading_days: Optional[int] = None
    simulated_entry_price: Optional[float] = None
    simulated_exit_price: Optional[float] = None
    simulated_exit_reason: Optional[str] = None
    simulated_return_pct: Optional[float] = None


class BacktestResultsResponse(BaseModel):
    """回测结果记录的分页列表响应。

    用于查询回测结果时返回分页数据，包含回测记录列表和分页信息。

    Attributes:
        total: 符合条件的回测记录总数
        page: 当前页码（从 1 开始）
        limit: 每页数量
        items: 当前页的回测结果列表
    """

    total: int
    page: int
    limit: int
    items: List[BacktestResultItem] = Field(default_factory=list)


class PerformanceMetrics(BaseModel):
    """按 scope / code / 评估窗口聚合的回测表现指标。

    该类用于汇总和展示回测的聚合表现指标，
    支持按不同维度（如全局、单股、不同评估窗口）进行统计分析。

    Attributes:
        scope: 聚合维度（如 "global" / "code" / "window" 等）
        code: 股票代码（仅当 scope 为单股时），可选
        eval_window_days: 评估窗口（交易日数）
        engine_version: 回测引擎版本号
        computed_at: 计算时间（ISO 格式），可选
        total_evaluations: 评估记录总数
        completed_count: 已完成评估的记录数
        insufficient_count: 数据不足的记录数
        long_count: 看多（long）的记录数
        cash_count: 空仓（cash）的记录数
        win_count: 盈利记录数
        loss_count: 亏损记录数
        neutral_count: 中性记录数
        direction_accuracy_pct: 方向判断准确率（百分比），可选
        win_rate_pct: 胜率（百分比），可选
        neutral_rate_pct: 中性率（百分比），可选
        avg_stock_return_pct: 平均股票收益率（百分比），可选
        avg_simulated_return_pct: 平均模拟收益率（百分比），可选
        stop_loss_trigger_rate: 止损触发率，可选
        take_profit_trigger_rate: 止盈触发率，可选
        ambiguous_rate: 模糊结果率，可选
        avg_days_to_first_hit: 平均首次触及天数，可选
        advice_breakdown: 按建议类型分组的统计，Dict 格式
        diagnostics: 诊断信息，Dict 格式
    """

    scope: str
    code: Optional[str] = None
    eval_window_days: int
    engine_version: str
    computed_at: Optional[str] = None

    total_evaluations: int
    completed_count: int
    insufficient_count: int
    long_count: int
    cash_count: int
    win_count: int
    loss_count: int
    neutral_count: int

    direction_accuracy_pct: Optional[float] = None
    win_rate_pct: Optional[float] = None
    neutral_rate_pct: Optional[float] = None
    avg_stock_return_pct: Optional[float] = None
    avg_simulated_return_pct: Optional[float] = None

    stop_loss_trigger_rate: Optional[float] = None
    take_profit_trigger_rate: Optional[float] = None
    ambiguous_rate: Optional[float] = None
    avg_days_to_first_hit: Optional[float] = None

    advice_breakdown: Dict[str, Any] = Field(default_factory=dict)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
