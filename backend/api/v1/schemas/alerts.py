# -*- coding: utf-8 -*-
"""告警 API 的 Pydantic Schema。

围绕告警规则、触发记录、通知投递记录三个核心对象，定义请求与响应模型。

设计要点：
- 规则的 ``parameters``、``cooldown_policy``、``notification_policy`` 字段一律
  保持为 ``Dict[str, Any]``，便于不同 ``alert_type`` 逐步扩展自己的配置结构，
  无需每次都修改 Schema。
- 触发记录与通知记录都采用分页列表响应（``*ListResponse``），便于前端按页加载。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# 告警目标范围类型：目前仅支持单只股票的告警
TargetScopeValue = Literal["single_symbol"]
"""告警目标范围类型枚举。

currently supported values:
    - "single_symbol": 针对单只股票的告警
"""

# 告警严重级别类型
SeverityValue = Literal["info", "warning", "critical"]
"""告警严重级别类型枚举。

取值说明：
    - info: 信息级别，仅作为提示
    - warning: 警告级别，需要关注
    - critical: 严重级别，需要立即处理
"""

# 告警规则试跑（dry-run）状态类型
DryRunStatusValue = Literal["triggered", "not_triggered", "evaluation_error"]
"""告警规则试跑（dry-run）状态类型枚举。

取值说明：
    - triggered: 规则条件被触发
    - not_triggered: 规则条件未被触发
    - evaluation_error: 评估过程中发生错误
"""


class AlertRuleCreateRequest(BaseModel):
    """创建告警规则的请求体。

    该类用于封装创建告警规则时所需的全部参数，包括规则名称、目标范围、
    告警类型、触发参数、严重级别以及冷却和通知策略等。

    Attributes:
        name: 规则名称，可选，最大长度 64 字符
        target_scope: 目标范围，目前仅支持 "single_symbol"（单只股票）
        target: 告警目标（股票代码），必填，1-64 字符
        alert_type: 告警类型标识，必填，1-32 字符
        parameters: 告警参数，Dict 格式，不同 alert_type 可扩展自己的配置结构
        severity: 严重级别，可选 "info" / "warning" / "critical"，默认 "warning"
        enabled: 是否启用该规则，默认 True
        cooldown_policy: 冷却策略，Dict 格式，用于控制告警触发频率
        notification_policy: 通知策略，Dict 格式，用于配置通知渠道和方式
    """

    name: Optional[str] = Field(None, max_length=64)
    target_scope: TargetScopeValue = "single_symbol"
    target: str = Field(..., min_length=1, max_length=64)
    alert_type: str = Field(..., min_length=1, max_length=32)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    severity: SeverityValue = "warning"
    enabled: bool = True
    cooldown_policy: Optional[Dict[str, Any]] = None
    notification_policy: Optional[Dict[str, Any]] = None


class AlertRuleUpdateRequest(BaseModel):
    """对已有告警规则做局部更新的请求体（仅修改显式给出的字段）。

    与 AlertRuleCreateRequest 不同，本类所有字段均为 Optional，
    允许客户端只提交需要修改的字段，服务端仅更新非 None 的字段。

    Attributes:
        name: 规则名称，可选，最大长度 64 字符
        target_scope: 目标范围，可选
        target: 告警目标（股票代码），可选，1-64 字符
        alert_type: 告警类型标识，可选，1-32 字符
        parameters: 告警参数，可选
        severity: 严重级别，可选
        enabled: 是否启用，可选
        cooldown_policy: 冷却策略，可选
        notification_policy: 通知策略，可选
    """

    name: Optional[str] = Field(None, max_length=64)
    target_scope: Optional[TargetScopeValue] = None
    target: Optional[str] = Field(None, min_length=1, max_length=64)
    alert_type: Optional[str] = Field(None, min_length=1, max_length=32)
    parameters: Optional[Dict[str, Any]] = None
    severity: Optional[SeverityValue] = None
    enabled: Optional[bool] = None
    cooldown_policy: Optional[Dict[str, Any]] = None
    notification_policy: Optional[Dict[str, Any]] = None


class AlertRuleItem(BaseModel):
    """返回给 API 客户端的告警规则完整表示。

    该类包含告警规则的全部字段，用于在列表和详情接口中返回给前端展示。

    Attributes:
        id: 规则唯一标识
        name: 规则名称
        target_scope: 目标范围（如 "single_symbol"）
        target: 告警目标（股票代码）
        alert_type: 告警类型标识
        parameters: 告警参数配置
        severity: 严重级别
        enabled: 是否启用
        source: 规则来源（如 "user" / "system"）
        cooldown_policy: 冷却策略配置
        notification_policy: 通知策略配置
        created_at: 创建时间（ISO 格式），可选
        updated_at: 最后更新时间（ISO 格式），可选
    """

    id: int
    name: str
    target_scope: str
    target: str
    alert_type: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    severity: str
    enabled: bool
    source: str
    cooldown_policy: Optional[Dict[str, Any]] = None
    notification_policy: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AlertRuleListResponse(BaseModel):
    """告警规则列表的分页响应。

    用于在查询告警规则列表时返回分页结果，包含当前页的数据和分页信息。

    Attributes:
        items: 当前页的告警规则列表
        total: 符合条件的告警规则总数
        page: 当前页码（从 1 开始）
        page_size: 每页数量
    """

    items: List[AlertRuleItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class AlertDeleteResponse(BaseModel):
    """告警规则删除操作的概要响应。

    用于返回删除操作的结果统计。

    Attributes:
        deleted: 实际删除的告警规则数量
    """

    deleted: int


class AlertRuleTestResponse(BaseModel):
    """对单条告警规则进行试跑（dry-run）的评估结果。

    用于在不实际触发告警的情况下，测试某条规则的触发条件和效果。

    Attributes:
        rule_id: 被测试的规则 ID
        status: 试跑状态（"triggered" / "not_triggered" / "evaluation_error"）
        triggered: 是否触发告警（True / False）
        observed_value: 观察到的值（如价格、指标等），可选
        message: 试跑结果描述信息
    """

    rule_id: int
    status: DryRunStatusValue
    triggered: bool
    observed_value: Optional[Any] = None
    message: str


class AlertTriggerItem(BaseModel):
    """单条历史告警触发事件。

    记录某条告警规则被触发时的详细信息，用于历史查询和审计。

    Attributes:
        id: 触发记录唯一标识
        rule_id: 关联的告警规则 ID，可选
        target: 触发目标（股票代码）
        observed_value: 触发时观察到的值（如价格），可选
        threshold: 触发阈值，可选
        reason: 触发原因描述，可选
        data_source: 数据来源，可选
        data_timestamp: 数据时间戳（ISO 格式），可选
        triggered_at: 触发时间（ISO 格式），可选
        status: 触发状态（如 "triggered" / "acknowledged" / "resolved"）
        diagnostics: 诊断信息，用于排查触发详情，可选
    """

    id: int
    rule_id: Optional[int] = None
    target: str
    observed_value: Optional[float] = None
    threshold: Optional[float] = None
    reason: Optional[str] = None
    data_source: Optional[str] = None
    data_timestamp: Optional[str] = None
    triggered_at: Optional[str] = None
    status: str
    diagnostics: Optional[str] = None


class AlertTriggerListResponse(BaseModel):
    """告警触发历史的分页响应。

    用于查询告警触发历史记录时返回分页结果。

    Attributes:
        items: 当前页的告警触发记录列表
        total: 符合条件的触发记录总数
        page: 当前页码（从 1 开始）
        page_size: 每页数量
    """

    items: List[AlertTriggerItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class AlertNotificationItem(BaseModel):
    """由告警触发产生的单次通知投递记录。

    记录某次告警触发后，通知被投递到某个渠道的详细信息，
    用于追踪通知的投递状态和排查通知失败问题。

    Attributes:
        id: 通知记录唯一标识
        trigger_id: 关联的告警触发记录 ID，可选
        channel: 通知渠道（如 "email" / "sms" / "webhook" 等）
        attempt: 投递尝试次数
        success: 是否投递成功
        error_code: 错误码（投递失败时），可选
        retryable: 是否可重试
        latency_ms: 投递延迟（毫秒），可选
        diagnostics: 诊断信息，可选
        created_at: 创建时间（ISO 格式），可选
    """

    id: int
    trigger_id: Optional[int] = None
    channel: str
    attempt: int
    success: bool
    error_code: Optional[str] = None
    retryable: bool
    latency_ms: Optional[int] = None
    diagnostics: Optional[str] = None
    created_at: Optional[str] = None


class AlertNotificationListResponse(BaseModel):
    """告警通知投递记录的分页响应。

    用于查询告警通知投递记录时返回分页结果。

    Attributes:
        items: 当前页的通知投递记录列表
        total: 符合条件的通知记录总数
        page: 当前页码（从 1 开始）
        page_size: 每页数量
    """

    items: List[AlertNotificationItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
