# -*- coding: utf-8 -*-
"""Schemas for LLM usage tracking API.

这些响应面向后台/账号用量页面，按时间窗口汇总调用次数和 token 消耗，并按调用
类型、模型维度拆分。字段保持简单数值，具体费用换算由账单/额度服务处理。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class CallTypeBreakdown(BaseModel):
    """按业务调用类型（call type）汇总的用量。"""

    call_type: str = Field(..., description="'analysis' | 'agent' | 'market_review'")
    calls: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int


class ModelBreakdown(BaseModel):
    """按 LLM 模型名称汇总的用量。"""

    model: str
    calls: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int
    max_total_tokens: int = 0


class UsageCallRecord(BaseModel):
    """单次 LLM 调用的原始记录，用于用量明细展示。"""

    id: int
    called_at: str = Field(..., description="ISO datetime string")
    call_type: str
    model: str
    stock_code: Optional[str] = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class UsageSummaryResponse(BaseModel):
    """指定报表周期内的顶层 LLM 用量概要响应。"""

    period: str = Field(..., description="'today' | 'month' | 'all'")
    from_date: str = Field(..., description="ISO date string")
    to_date: str = Field(..., description="ISO date string")
    total_calls: int
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int
    by_call_type: List[CallTypeBreakdown]
    by_model: List[ModelBreakdown]


class UsageDashboardResponse(UsageSummaryResponse):
    """用量页面聚合响应：除概要外，还附带近期调用明细列表。"""

    recent_calls: List[UsageCallRecord]
