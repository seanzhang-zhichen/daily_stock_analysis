# -*- coding: utf-8 -*-
"""LLM 用量追踪 API 的数据模型（Schema）。

这些响应面向后台/账号用量页面，按时间窗口汇总调用次数和 token 消耗，并按调用
类型、模型维度拆分。字段保持简单数值，具体费用换算由账单/额度服务处理。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class CallTypeBreakdown(BaseModel):
    """按业务调用类型（call type）汇总的用量。"""

    # 调用类型标识，例如 'analysis'、'agent'、'market_review'
    call_type: str = Field(..., description="'analysis' | 'agent' | 'market_review'")
    # 该类型下的调用总次数
    calls: int
    # 提示词（输入）消耗的 token 数量
    prompt_tokens: int = 0
    # 补全（输出）消耗的 token 数量
    completion_tokens: int = 0
    # 该类型下消耗的总 token 数（prompt + completion）
    total_tokens: int


class ModelBreakdown(BaseModel):
    """按 LLM 模型名称汇总的用量。"""

    # 模型名称，例如 'gpt-4o'、'claude-3-sonnet'
    model: str
    # 该模型下的调用总次数
    calls: int
    # 提示词（输入）消耗的 token 数量
    prompt_tokens: int = 0
    # 补全（输出）消耗的 token 数量
    completion_tokens: int = 0
    # 该模型下消耗的总 token 数
    total_tokens: int
    # 单次调用中消耗的最大 token 数（用于评估峰值）
    max_total_tokens: int = 0


class UsageCallRecord(BaseModel):
    """单次 LLM 调用的原始记录，用于用量明细展示。"""

    # 数据库自增 ID
    id: int
    # 调用发生的时间，ISO 8601 格式字符串
    called_at: str = Field(..., description="ISO datetime string")
    # 该次调用的业务类型
    call_type: str
    # 使用的 LLM 模型名称
    model: str
    # 关联的股票代码（如果有），用于分析类调用
    stock_code: Optional[str] = None
    # 提示词 token 消耗
    prompt_tokens: int
    # 补全 token 消耗
    completion_tokens: int
    # 总 token 消耗
    total_tokens: int


class UsageSummaryResponse(BaseModel):
    """指定报表周期内的顶层 LLM 用量概要响应。"""

    # 报表周期标识，例如 'today'、'month'、'all'
    period: str = Field(..., description="'today' | 'month' | 'all'")
    # 统计起始日期，ISO 8601 格式
    from_date: str = Field(..., description="ISO date string")
    # 统计结束日期，ISO 8601 格式
    to_date: str = Field(..., description="ISO date string")
    # 该周期内总调用次数
    total_calls: int
    # 提示词总 token 消耗
    total_prompt_tokens: int = 0
    # 补全总 token 消耗
    total_completion_tokens: int = 0
    # 总 token 消耗
    total_tokens: int
    # 按调用类型拆分的用量列表
    by_call_type: List[CallTypeBreakdown]
    # 按模型拆分的用量列表
    by_model: List[ModelBreakdown]


class UsageDashboardResponse(UsageSummaryResponse):
    """用量页面聚合响应：除概要外，还附带近期调用明细列表。"""

    # 最近若干条调用记录明细
    recent_calls: List[UsageCallRecord]
