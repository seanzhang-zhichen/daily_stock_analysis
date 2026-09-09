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


TargetScopeValue = Literal["single_symbol"]
SeverityValue = Literal["info", "warning", "critical"]
DryRunStatusValue = Literal["triggered", "not_triggered", "evaluation_error"]


class AlertRuleCreateRequest(BaseModel):
    """创建告警规则的请求体。"""

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
    """对已有告警规则做局部更新的请求体（仅修改显式给出的字段）。"""

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
    """返回给 API 客户端的告警规则完整表示。"""

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
    """告警规则列表的分页响应。"""

    items: List[AlertRuleItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class AlertDeleteResponse(BaseModel):
    """告警规则删除操作的概要响应。"""

    deleted: int


class AlertRuleTestResponse(BaseModel):
    """对单条告警规则进行试跑（dry-run）的评估结果。"""

    rule_id: int
    status: DryRunStatusValue
    triggered: bool
    observed_value: Optional[Any] = None
    message: str


class AlertTriggerItem(BaseModel):
    """单条历史告警触发事件。"""

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
    """告警触发历史的分页响应。"""

    items: List[AlertTriggerItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class AlertNotificationItem(BaseModel):
    """由告警触发产生的单次通知投递记录。"""

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
    """告警通知投递记录的分页响应。"""

    items: List[AlertNotificationItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
