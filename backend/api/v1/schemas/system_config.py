# -*- coding: utf-8 -*-
"""System configuration API schemas.

配置接口同时服务动态表单渲染、.env 导入导出、运行时校验和连通性测试。敏感值
通过 ``mask_token`` 与 ``is_masked`` 表达，schema 只描述契约，不在这里持久化
或解密真实配置。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

LLMCapabilityCheck = Literal["json", "tools", "vision", "stream"]
# 支持测试连通性的通知渠道枚举：覆盖国内外主流渠道与自定义 webhook
NotificationTestChannel = Literal[
    "wechat",
    "feishu",
    "telegram",
    "email",
    "pushover",
    "ntfy",
    "gotify",
    "pushplus",
    "serverchan3",
    "custom",
    "discord",
    "slack",
    "astrbot",
]


class SystemConfigOption(BaseModel):
    """下拉选项元数据：用于前端下拉控件渲染。"""

    label: str
    value: str


class SystemConfigDocLink(BaseModel):
    """字段帮助面板中的文档链接元数据。"""

    label: str
    href: str


class SystemConfigFieldSchema(BaseModel):
    """单个配置字段的元数据契约。"""

    key: str = Field(..., description="Configuration key name")
    title: Optional[str] = Field(None, description="Display title")
    description: Optional[str] = Field(None, description="Field description")
    category: Literal["base", "data_source", "ai_model", "notification", "system", "agent", "backtest", "uncategorized"]
    data_type: Literal["string", "integer", "number", "boolean", "array", "json", "time"]
    ui_control: Literal["text", "password", "number", "select", "textarea", "switch", "time"]
    is_sensitive: bool
    is_required: bool
    is_editable: bool
    default_value: Optional[str] = None
    options: List[str | SystemConfigOption] = Field(default_factory=list)
    validation: Dict[str, Any] = Field(default_factory=dict)
    display_order: int
    help_key: Optional[str] = Field(None, description="Stable localization key for detailed help content")
    examples: List[str] = Field(default_factory=list, description="Safe example values for help panels")
    docs: List[SystemConfigDocLink] = Field(default_factory=list, description="Related documentation links")
    warning_codes: List[str] = Field(default_factory=list, description="Stable warning identifiers for help panels")


class SystemConfigCategorySchema(BaseModel):
    """配置字段按类别分组后的容器。"""

    category: str
    title: str
    description: Optional[str] = None
    display_order: int
    fields: List[SystemConfigFieldSchema]


class SystemConfigSchemaResponse(BaseModel):
    """返回给前端用于动态渲染表单的整体 schema。"""

    schema_version: str
    categories: List[SystemConfigCategorySchema]


class SystemConfigItem(BaseModel):
    """单条配置项：当前值 + 关联字段元数据（敏感值已脱敏）。"""

    model_config = ConfigDict(populate_by_name=True)

    key: str
    value: str
    raw_value_exists: bool
    is_masked: bool
    schema_: Optional[SystemConfigFieldSchema] = Field(default=None, alias="schema")


class SystemConfigResponse(BaseModel):
    """读取当前生效配置的响应体。"""

    config_version: str
    mask_token: str
    items: List[SystemConfigItem]
    updated_at: Optional[str] = None


class SetupStatusCheck(BaseModel):
    """首启引导阶段的一项就绪检查。"""

    key: str
    title: str
    category: Literal["base", "ai_model", "agent", "notification", "system"]
    required: bool
    status: Literal["configured", "inherited", "optional", "needs_action"]
    message: str
    next_step: Optional[str] = None


class SetupStatusResponse(BaseModel):
    """首启引导状态总览。"""

    is_complete: bool
    ready_for_smoke: bool
    required_missing_keys: List[str] = Field(default_factory=list)
    next_step_key: Optional[str] = None
    checks: List[SetupStatusCheck] = Field(default_factory=list)


class ExportSystemConfigResponse(BaseModel):
    """导出 `.env` 原始备份内容的载荷。"""

    content: str
    config_version: str
    updated_at: Optional[str] = None


class SystemConfigUpdateItem(BaseModel):
    """单条键值更新项。"""

    key: str
    value: str


class UpdateSystemConfigRequest(BaseModel):
    """批量更新配置请求：带乐观锁版本号与脱敏令牌支持。"""

    config_version: str
    mask_token: str = "******"
    reload_now: bool = True
    items: List[SystemConfigUpdateItem] = Field(..., min_length=1)


class UpdateSystemConfigResponse(BaseModel):
    """批量更新配置的结果响应。"""

    success: bool
    config_version: str
    applied_count: int
    skipped_masked_count: int
    reload_triggered: bool
    updated_keys: List[str]
    warnings: List[str] = Field(default_factory=list)


class ValidateSystemConfigRequest(BaseModel):
    """配置项校验请求载荷。"""

    items: List[SystemConfigUpdateItem] = Field(..., min_length=1)


class ImportSystemConfigRequest(BaseModel):
    """从 `.env` 原始备份导入配置的请求载荷。"""

    config_version: str
    content: str
    reload_now: bool = True


class ConfigValidationIssue(BaseModel):
    """单条配置校验问题详情。"""

    key: str
    code: str
    message: str
    severity: Literal["error", "warning"]
    expected: Optional[str] = None
    actual: Optional[str] = None


class ValidateSystemConfigResponse(BaseModel):
    """配置校验结果响应。"""

    valid: bool
    issues: List[ConfigValidationIssue]


class TestLLMChannelRequest(BaseModel):
    """在不持久化的情况下，临时测试某个 LLM 通道连通性的请求载荷。"""

    name: str = "channel"
    protocol: str = "openai"
    base_url: str = ""
    api_key: str = ""
    models: List[str] = Field(default_factory=list)
    enabled: bool = True
    timeout_seconds: float = 20.0
    capability_checks: List[LLMCapabilityCheck] = Field(default_factory=list)


class LLMCapabilityCheckResult(BaseModel):
    """单项能力冒烟测试的运行时结果（如 JSON 模式、工具调用、流式响应等）。"""

    status: Literal["passed", "failed", "skipped"]
    message: str
    error_code: Optional[str] = None
    stage: str
    retryable: bool = False
    latency_ms: Optional[int] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class TestLLMChannelResponse(BaseModel):
    """单条 LLM 通道连通性测试响应。"""

    success: bool
    message: str
    error: Optional[str] = None
    error_code: Optional[str] = None
    stage: Optional[str] = None
    retryable: Optional[bool] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    resolved_protocol: Optional[str] = None
    resolved_model: Optional[str] = None
    latency_ms: Optional[int] = None
    capability_results: Dict[str, LLMCapabilityCheckResult] = Field(default_factory=dict)


class NotificationTestAttempt(BaseModel):
    """单条通知投递尝试的结果。"""

    channel: NotificationTestChannel
    success: bool
    message: str
    target: Optional[str] = None
    error_code: Optional[str] = None
    stage: str = "notification_send"
    retryable: bool = False
    latency_ms: Optional[int] = None
    http_status: Optional[int] = None


class TestNotificationChannelRequest(BaseModel):
    """使用临时配置对某个通知通道进行连通性测试的请求载荷。"""

    channel: NotificationTestChannel
    items: List[SystemConfigUpdateItem] = Field(default_factory=list)
    mask_token: str = "******"
    title: str = Field(default="DSA 通知测试", min_length=1, max_length=80)
    content: str = Field(default="这是一条来自 DSA Web 设置页的通知测试消息。", min_length=1, max_length=1000)
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)


class TestNotificationChannelResponse(BaseModel):
    """通知通道连通性测试响应。"""

    success: bool
    message: str
    error_code: Optional[str] = None
    stage: Optional[str] = None
    retryable: bool = False
    latency_ms: Optional[int] = None
    attempts: List[NotificationTestAttempt] = Field(default_factory=list)


class DiscoverLLMChannelModelsRequest(BaseModel):
    """在不保存的前提下探测某 LLM 通道可用模型列表的请求载荷。"""

    name: str = "channel"
    protocol: str = "openai"
    base_url: str = ""
    api_key: str = ""
    models: List[str] = Field(default_factory=list)
    timeout_seconds: float = 20.0


class DiscoverLLMChannelModelsResponse(BaseModel):
    """LLM 通道可用模型探测结果响应。"""

    success: bool
    message: str
    error: Optional[str] = None
    error_code: Optional[str] = None
    stage: Optional[str] = None
    retryable: Optional[bool] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    resolved_protocol: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    latency_ms: Optional[int] = None


class SystemConfigValidationErrorResponse(BaseModel):
    """配置更新校验失败时的错误响应。"""

    error: str
    message: str
    issues: List[ConfigValidationIssue]


class SystemConfigConflictResponse(BaseModel):
    """乐观锁版本冲突时的错误响应。"""

    error: str
    message: str
    current_config_version: str
