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
"""AI 决策信号建议的操作类型枚举。

取值说明：
    - buy: 买入，建议新建仓位
    - add: 加仓，建议在现有仓位基础上增持
    - hold: 持有，建议保持当前仓位不变
    - reduce: 减仓，建议减少部分仓位
    - sell: 卖出，建议清仓该标的
    - watch: 观察，建议密切关注但暂不操作
    - avoid: 回避，建议避免介入该标的
    - alert: 仅提醒，仅作为信息提示，不给出明确操作建议
"""

# 信号生命周期状态：active 有效 / expired 过期 / invalidated 失效 / closed 关闭 / archived 归档
DecisionSignalStatus = Literal["active", "expired", "invalidated", "closed", "archived"]
"""AI 决策信号的生命周期状态枚举。

取值说明：
    - active: 有效，信号当前处于活跃状态
    - expired: 过期，信号已超过预设有效期
    - invalidated: 失效，因触发失效条件而提前终止
    - closed: 关闭，信号被手动或系统关闭
    - archived: 归档，信号已归档，不再参与日常计算
"""


class DecisionSignalItem(BaseModel):
    """单条 AI 决策信号详情模型。

    该类描述一条完整的 AI 决策信号，包含标的代码、建议操作、
    价格区间、风险摘要等核心信息。前端表格和详情面板按此模型渲染。

    Attributes:
        id: 信号唯一标识
        stock_code: 股票代码
        stock_name: 股票名称
        market: 市场标识（如 'cn', 'hk', 'us'）
        source_type: 信号来源类型
        source_report_id: 来源报告 ID
        trace_id: 追踪 ID，用于链路追踪
        trigger_source: 触发来源
        action: 建议操作类型（买入、卖出等）
        action_label: 操作标签的本地化文本
        confidence: 置信度（0-1 之间）
        score: 综合评分
        horizon: 时间周期（如 '1d', '1w', '1m'）
        entry_low: 建议买入/加仓价格下限
        entry_high: 建议买入/加仓价格上限
        stop_loss: 止损价格
        target_price: 目标价格
        invalidation: 失效条件描述
        watch_conditions: 观察条件描述
        reason: 信号生成原因/逻辑说明
        risk_summary: 风险摘要
        catalyst_summary: 催化剂摘要
        evidence: 支持信号的证据数据
        data_quality_summary: 数据质量摘要
        metadata: 附加元数据
        plan_quality: 方案质量等级
        status: 信号当前生命周期状态
        expires_at: 信号过期时间（ISO 格式）
        created_at: 信号创建时间（ISO 格式）
        updated_at: 信号最后更新时间（ISO 格式）
    """

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
    """AI 决策信号分页列表响应模型。

    该类用于封装 AI 决策信号的分页查询结果，包含信号列表和分页信息。

    Attributes:
        items: 信号列表，每条信号为一个 DecisionSignalItem
        total: 符合条件的信号总数
        page: 当前页码（从 1 开始）
        page_size: 每页数量
    """

    items: List[DecisionSignalItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class DecisionSignalLatestResponse(BaseModel):
    """某只股票最新有效信号集合响应模型。

    该类用于封装查询某只股票最新有效信号的返回结果。

    Attributes:
        items: 该股票最新的有效信号列表
    """

    items: List[DecisionSignalItem] = Field(default_factory=list)


class DecisionSignalStatusUpdateRequest(BaseModel):
    """更新 AI 决策信号状态的请求载荷模型。

    该类用于封装更新信号状态的请求参数。

    Attributes:
        status: 要更新的目标状态
    """

    status: DecisionSignalStatus


class DecisionSignalFeedbackRequest(BaseModel):
    """AI 决策信号用户反馈请求模型。

    该类用于封装用户对信号的反馈信息，帮助改进信号质量。

    Attributes:
        feedback_value: 反馈值，'useful'（有用）或 'not_useful'（无用）
        reason_code: 反馈原因代码，用于分类统计
        note: 用户补充说明，最大长度 1000 字符
        source: 反馈来源，'web'（网页）或 'api'（API）
    """

    feedback_value: Literal["useful", "not_useful"]
    reason_code: Optional[str] = Field(None, max_length=64)
    note: Optional[str] = Field(None, max_length=1000)
    source: Literal["web", "api"] = "api"


class DecisionSignalFeedbackItem(BaseModel):
    """AI 决策信号用户反馈记录模型。

    该类用于表示单条用户反馈的完整记录。

    Attributes:
        signal_id: 关联的信号 ID
        feedback_value: 反馈值，'useful' 或 'not_useful'
        reason_code: 反馈原因代码
        note: 用户补充说明
        source: 反馈来源
        created_at: 反馈创建时间（ISO 格式）
        updated_at: 反馈最后更新时间（ISO 格式）
    """

    signal_id: int
    feedback_value: Optional[Literal["useful", "not_useful"]] = None
    reason_code: Optional[str] = None
    note: Optional[str] = None
    source: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DecisionSignalReassessRequest(BaseModel):
    """AI 决策信号重新评估请求模型。

    该类用于触发对某条信号的重新评估流程。

    Attributes:
        source_report_id: 来源报告 ID，必须大于 0
        persist: 是否将评估结果持久化到数据库
    """

    source_report_id: int = Field(..., gt=0)
    persist: bool = False


class DecisionSignalReassessResponse(BaseModel):
    """AI 决策信号重新评估响应模型。

    该类用于封装重新评估后的返回结果，包含预览信息和生成的信号。

    Attributes:
        preview: 评估预览数据，包含评估过程的中间结果
        item: 重新评估后生成的信号对象
        created: 是否成功创建了新信号
        warnings: 评估过程中的警告信息列表
    """

    preview: Optional[Dict[str, Any]] = None
    item: Optional[DecisionSignalItem] = None
    created: bool = False
    warnings: List[str] = Field(default_factory=list)


class DecisionSignalSyncResponse(BaseModel):
    """从分析历史同步 AI 信号的结果响应模型。

    该类用于封装从分析历史数据同步 AI 信号后的返回结果。

    Attributes:
        created: 成功创建的信号数量
    """

    created: int


class DecisionSignalMutationResponse(BaseModel):
    """AI 决策信号变更后的统一响应模型。

    该类用于封装信号变更操作（如创建、更新、删除）后的返回结果，
    包含变更后的最新信号信息。

    Attributes:
        item: 变更后的最新信号对象
    """

    item: DecisionSignalItem


class DecisionSignalOutcomeRunRequest(BaseModel):
    """对某条信号触发"出效评估"任务的请求载荷模型。

    该类用于触发对指定信号的效果评估任务，评估信号在不同时间窗口下的表现。

    Attributes:
        horizons: 评估时间窗口列表，可选值包括 "1d"（1天）、"3d"（3天）、
            "5d"（5天）、"10d"（10天）
        force: 是否强制重新评估，即使已有评估结果
    """

    horizons: Optional[List[Literal["1d", "3d", "5d", "10d"]]] = None
    force: bool = False


class DecisionSignalOutcomeItem(BaseModel):
    """单条信号在某评估窗口下的"出效"评估记录模型。

    该类用于记录信号在特定评估窗口下的表现数据，包括价格变动、
    方向判断正确性等关键指标。

    Attributes:
        id: 评估记录唯一标识
        signal_id: 关联的信号 ID
        horizon: 评估时间窗口（如 '1d', '3d', '5d', '10d'）
        engine_version: 评估引擎版本号
        eval_status: 评估状态
        outcome: 评估结果描述
        direction_expected: 预期方向（如 'up', 'down'）
        direction_correct: 方向判断是否正确
        unable_reason: 无法评估的原因
        anchor_date: 评估基准日期（ISO 格式）
        eval_window_days: 评估窗口天数
        start_price: 评估起始价格
        end_close: 评估期末收盘价
        max_high: 评估期内最高价
        min_low: 评估期内最低价
        stock_return_pct: 股票收益率（百分比）
        action: 信号建议的操作类型
        market: 市场标识
        holding_state: 持仓状态
        created_at: 记录创建时间（ISO 格式）
        updated_at: 记录最后更新时间（ISO 格式）
    """

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
    """批量出效评估任务的执行结果响应模型。

    该类用于封装批量执行出效评估任务后的统计结果。

    Attributes:
        items: 评估结果列表
        evaluated: 已评估的信号数量
        created: 新创建的评估记录数量
        updated: 更新的评估记录数量
        skipped: 跳过的信号数量
        engine_version: 评估引擎版本号
    """

    items: List[DecisionSignalOutcomeItem] = Field(default_factory=list)
    evaluated: int
    created: int
    updated: int
    skipped: int
    engine_version: str


class DecisionSignalOutcomeListResponse(BaseModel):
    """出效评估记录的分页列表响应模型。

    该类用于封装出效评估记录的分页查询结果。

    Attributes:
        items: 评估记录列表
        total: 符合条件的记录总数
        page: 当前页码（从 1 开始）
        page_size: 每页数量
    """

    items: List[DecisionSignalOutcomeItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class DecisionSignalOutcomeStatsResponse(BaseModel):
    """出效评估统计结果响应模型。

    该类用于封装出效评估的汇总统计数据，包括命中率、收益率等关键指标。

    Attributes:
        engine_version: 评估引擎版本号
        horizon: 评估时间窗口
        total: 评估记录总数
        completed: 已完成评估的记录数
        unable: 无法评估的记录数
        hit: 方向判断正确的记录数
        miss: 方向判断错误的记录数
        hit_rate_pct: 命中率（百分比）
        avg_stock_return_pct: 平均股票收益率（百分比）
        by_action: 按操作类型分组的统计数据
    """

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
