# -*- coding: utf-8 -*-
"""AI 决策信号（Decision Signal）模块对外的 Pydantic 契约。

涵盖单条信号详情、信号列表/最新查询响应、状态更新、出效评估请求与结果等。
由 `backend/api/v1/endpoints/decision_signals.py` 路由层装配使用，前端
表格与详情面板按此契约渲染。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# 信号建议的操作类型：buy 买入 / add 加仓 / hold 持有 / reduce 减仓 / sell 卖出 /
# watch 观察 / avoid 回避 / alert 仅提醒
DecisionAction = Literal["buy", "add", "hold", "reduce", "sell", "watch", "avoid", "alert"]
# 信号生命周期状态：active 有效 / expired 过期 / invalidated 失效 / closed 关闭 / archived 归档
DecisionSignalStatus = Literal["active", "expired", "invalidated", "closed", "archived"]


class DecisionSignalItem(BaseModel):
    """单条 AI 决策信号详情。"""

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
    """AI 决策信号分页列表响应。"""

    items: List[DecisionSignalItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class DecisionSignalLatestResponse(BaseModel):
    """某只股票最新有效信号集合响应。"""

    items: List[DecisionSignalItem] = Field(default_factory=list)


class DecisionSignalStatusUpdateRequest(BaseModel):
    """更新 AI 决策信号状态的请求载荷。"""

    status: DecisionSignalStatus

class DecisionSignalFeedbackRequest(BaseModel):
    feedback_value: Literal["useful", "not_useful"]
    reason_code: Optional[str] = Field(None, max_length=64)
    note: Optional[str] = Field(None, max_length=1000)
    source: Literal["web", "api"] = "api"

class DecisionSignalFeedbackItem(BaseModel):
    signal_id: int
    feedback_value: Optional[Literal["useful", "not_useful"]] = None
    reason_code: Optional[str] = None
    note: Optional[str] = None
    source: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class DecisionSignalReassessRequest(BaseModel):
    source_report_id: int = Field(..., gt=0)
    persist: bool = False

class DecisionSignalReassessResponse(BaseModel):
    preview: Optional[Dict[str, Any]] = None
    item: Optional[DecisionSignalItem] = None
    created: bool = False
    warnings: List[str] = Field(default_factory=list)


class DecisionSignalSyncResponse(BaseModel):
    """从分析历史同步 AI 信号的结果响应。"""

    created: int


class DecisionSignalMutationResponse(BaseModel):
    """AI 决策信号变更后的统一返回（包含变更后的最新条目）。"""

    item: DecisionSignalItem


class DecisionSignalOutcomeRunRequest(BaseModel):
    """对某条信号触发"出效评估"任务的请求载荷。"""

    horizons: Optional[List[Literal["1d", "3d", "5d", "10d"]]] = None
    force: bool = False


class DecisionSignalOutcomeItem(BaseModel):
    """单条信号在某评估窗口下的"出效"评估记录。"""

    id: int
    signal_id: int
    horizon: str
    engine_version: str
    eval_status: str
    outcome: Optional[str] = None
    direction_expected: Optional[str] = None
    direction_correct: Optional[bool] = None
    unable_reason: Optional[str] = None
    anchor_date: Optional[str] = None
    eval_window_days: Optional[int] = None
    start_price: Optional[float] = None
    end_close: Optional[float] = None
    max_high: Optional[float] = None
    min_low: Optional[float] = None
    stock_return_pct: Optional[float] = None
    action: Optional[str] = None
    market: Optional[str] = None
    holding_state: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DecisionSignalOutcomeRunResponse(BaseModel):
    """批量出效评估任务的执行结果响应。"""

    items: List[DecisionSignalOutcomeItem] = Field(default_factory=list)
    evaluated: int
    created: int
    updated: int
    skipped: int
    engine_version: str


class DecisionSignalOutcomeListResponse(BaseModel):
    """出效评估记录的分页列表响应。"""

    items: List[DecisionSignalOutcomeItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int

class DecisionSignalOutcomeStatsResponse(BaseModel):
    engine_version: str
    horizon: Optional[str] = None
    total: int
    completed: int
    unable: int
    hit: int
    miss: int
    hit_rate_pct: Optional[float] = None
    avg_stock_return_pct: Optional[float] = None
    by_action: Dict[str, Dict[str, int]] = Field(default_factory=dict)


__all__ = [
    "DecisionSignalItem",
    "DecisionSignalLatestResponse",
    "DecisionSignalListResponse",
    "DecisionSignalMutationResponse",
    "DecisionSignalOutcomeRunRequest",
    "DecisionSignalOutcomeItem",
    "DecisionSignalOutcomeRunResponse",
    "DecisionSignalOutcomeListResponse",
    "DecisionSignalStatusUpdateRequest",
    "DecisionSignalSyncResponse",
    "DecisionSignalFeedbackRequest",
    "DecisionSignalFeedbackItem",
    "DecisionSignalOutcomeStatsResponse",
    "DecisionSignalReassessRequest",
    "DecisionSignalReassessResponse",
]
