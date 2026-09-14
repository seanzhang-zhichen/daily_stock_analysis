# -*- coding: utf-8 -*-
"""System configuration API schemas.

配置接口同时服务动态表单渲染、.env 导入导出、运行时校验和连通性测试。敏感值
通过 ``mask_token`` 与 ``is_masked`` 表达，schema 只描述契约，不在这里持久化
或解密真实配置。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# LLM 能力检查类型：用于标识需要测试的 LLM 能力
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

    label: str  # 选项显示文本
    value: str  # 选项实际值


class SystemConfigDocLink(BaseModel):
    """字段帮助面板中的文档链接元数据。"""

    label: str  # 链接显示文本
    href: str  # 链接目标地址


class SystemConfigFieldSchema(BaseModel):
    """单个配置字段的元数据契约。"""

    key: str = Field(..., description="Configuration key name")  # 配置键名
    title: Optional[str] = Field(None, description="Display title")  # 显示标题
    description: Optional[str] = Field(None, description="Field description")  # 字段描述
    category: Literal["base", "data_source", "ai_model", "notification", "system", "agent", "backtest", "uncategorized"]  # 配置类别
    data_type: Literal["string", "integer", "number", "boolean", "array", "json", "time"]  # 数据类型
    ui_control: Literal["text", "password", "number", "select", "textarea", "switch", "time"]  # UI 控件类型
    is_sensitive: bool  # 是否为敏感字段
    is_required: bool  # 是否必填
    is_editable: bool  # 是否可编辑
    default_value: Optional[str] = None  # 默认值
    options: List[str | SystemConfigOption] = Field(default_factory=list)  # 下拉选项列表
    validation: Dict[str, Any] = Field(default_factory=dict)  # 校验规则
    display_order: int  # 显示顺序
    help_key: Optional[str] = Field(None, description="Stable localization key for detailed help content")  # 帮助内容的本地化键
    examples: List[str] = Field(default_factory=list, description="Safe example values for help panels")  # 示例值
    docs: List[SystemConfigDocLink] = Field(default_factory=list, description="Related documentation links")  # 相关文档链接
    warning_codes: List[str] = Field(default_factory=list, description="Stable warning identifiers for help panels")  # 警告标识符


class SystemConfigCategorySchema(BaseModel):
    """配置字段按类别分组后的容器。"""

    category: str  # 类别标识
    title: str  # 类别标题
    description: Optional[str] = None  # 类别描述
    display_order: int  # 显示顺序
    fields: List[SystemConfigFieldSchema]  # 该类别下的字段列表


class SystemConfigSchemaResponse(BaseModel):
    """返回给前端用于动态渲染表单的整体 schema。"""

    schema_version: str  # schema 版本号
    categories: List[SystemConfigCategorySchema]  # 类别列表


class SystemConfigItem(BaseModel):
    """单条配置项：当前值 + 关联字段元数据（敏感值已脱敏）。"""

    model_config = ConfigDict(populate_by_name=True)

    key: str  # 配置键名
    value: str  # 当前值（敏感值已脱敏）
    raw_value_exists: bool  # 原始值是否存在
    is_masked: bool  # 是否已脱敏
    schema_: Optional[SystemConfigFieldSchema] = Field(default=None, alias="schema")  # 关联的字段元数据


class SystemConfigResponse(BaseModel):
    """读取当前生效配置的响应体。"""

    config_version: str  # 配置版本号
    mask_token: str  # 脱敏令牌
    items: List[SystemConfigItem]  # 配置项列表
    updated_at: Optional[str] = None  # 更新时间


class SetupStatusCheck(BaseModel):
    """首启引导阶段的一项就绪检查。"""

    key: str  # 检查项标识
    title: str  # 检查项标题
    category: Literal["base", "ai_model", "agent", "notification", "system"]  # 检查项类别
    required: bool  # 是否必需
    status: Literal["configured", "inherited", "optional", "needs_action"]  # 状态
    message: str  # 状态消息
    next_step: Optional[str] = None  # 下一步操作


class SetupStatusResponse(BaseModel):
    """首启引导状态总览。"""

    is_complete: bool  # 是否完成
    ready_for_smoke: bool  # 是否可进行冒烟测试
    required_missing_keys: List[str] = Field(default_factory=list)  # 缺失的必需键
    next_step_key: Optional[str] = None  # 下一步键
    checks: List[SetupStatusCheck] = Field(default_factory=list)  # 检查项列表


class ExportSystemConfigResponse(BaseModel):
    """导出 `.env` 原始备份内容的载荷。"""

    content: str  # 备份内容
    config_version: str  # 配置版本号
    updated_at: Optional[str] = None  # 更新时间


class SystemConfigUpdateItem(BaseModel):
    """单条键值更新项。"""

    key: str  # 配置键名
    value: str  # 更新值


class UpdateSystemConfigRequest(BaseModel):
    """批量更新配置请求：带乐观锁版本号与脱敏令牌支持。"""

    config_version: str  # 配置版本号（乐观锁）
    mask_token: str = "******"  # 脱敏令牌
    reload_now: bool = True  # 是否立即重载
    items: List[SystemConfigUpdateItem] = Field(..., min_length=1)  # 更新项列表


class UpdateSystemConfigResponse(BaseModel):
    """批量更新配置的结果响应。"""

    success: bool  # 是否成功
    config_version: str  # 新的配置版本号
    applied_count: int  # 应用成功的数量
    skipped_masked_count: int  # 跳过脱敏的数量
    reload_triggered: bool  # 是否触发重载
    updated_keys: List[str]  # 已更新的键列表
    warnings: List[str] = Field(default_factory=list)  # 警告信息


class ValidateSystemConfigRequest(BaseModel):
    """配置项校验请求载荷。"""

    items: List[SystemConfigUpdateItem] = Field(..., min_length=1)  # 待校验的配置项列表


class ImportSystemConfigRequest(BaseModel):
    """从 `.env` 原始备份导入配置的请求载荷。"""

    config_version: str  # 配置版本号
    content: str  # 备份内容
    reload_now: bool = True  # 是否立即重载


class ConfigValidationIssue(BaseModel):
    """单条配置校验问题详情。"""

    key: str  # 配置键名
    code: str  # 问题代码
    message: str  # 问题描述
    severity: Literal["error", "warning"]  # 严重程度
    expected: Optional[str] = None  # 期望值
    actual: Optional[str] = None  # 实际值


class ValidateSystemConfigResponse(BaseModel):
    """配置校验结果响应。"""

    valid: bool  # 是否通过校验
    issues: List[ConfigValidationIssue]  # 问题列表


class TestLLMChannelRequest(BaseModel):
    """在不持久化的情况下，临时测试某个 LLM 通道连通性的请求载荷。"""

    name: str = "channel"  # 通道名称
    protocol: str = "openai"  # 协议类型
    base_url: str = ""  # 基础 URL
    api_key: str = ""  # API 密钥
    models: List[str] = Field(default_factory=list)  # 模型列表
    enabled: bool = True  # 是否启用
    timeout_seconds: float = 20.0  # 超时时间（秒）
    capability_checks: List[LLMCapabilityCheck] = Field(default_factory=list)  # 需要检查的能力列表


class LLMCapabilityCheckResult(BaseModel):
    """单项能力冒烟测试的运行时结果（如 JSON 模式、工具调用、流式响应等）。"""

    status: Literal["passed", "failed", "skipped"]  # 测试状态
    message: str  # 结果消息
    error_code: Optional[str] = None  # 错误代码
    stage: str  # 测试阶段
    retryable: bool = False  # 是否可重试
    latency_ms: Optional[int] = None  # 延迟（毫秒）
    details: Dict[str, Any] = Field(default_factory=dict)  # 详细信息


class TestLLMChannelResponse(BaseModel):
    """单条 LLM 通道连通性测试响应。"""

    success: bool  # 是否成功
    message: str  # 结果消息
    error: Optional[str] = None  # 错误信息
    error_code: Optional[str] = None  # 错误代码
    stage: Optional[str] = None  # 测试阶段
    retryable: Optional[bool] = None  # 是否可重试
    details: Dict[str, Any] = Field(default_factory=dict)  # 详细信息
    resolved_protocol: Optional[str] = None  # 解析后的协议
    resolved_model: Optional[str] = None  # 解析后的模型
    latency_ms: Optional[int] = None  # 延迟（毫秒）
    capability_results: Dict[str, LLMCapabilityCheckResult] = Field(default_factory=dict)  # 能力测试结果


class NotificationTestAttempt(BaseModel):
    """单条通知投递尝试的结果。"""

    channel: NotificationTestChannel  # 通知渠道
    success: bool  # 是否成功
    message: str  # 结果消息
    target: Optional[str] = None  # 目标地址
    error_code: Optional[str] = None  # 错误代码
    stage: str = "notification_send"  # 阶段
    retryable: bool = False  # 是否可重试
    latency_ms: Optional[int] = None  # 延迟（毫秒）
    http_status: Optional[int] = None  # HTTP 状态码


class TestNotificationChannelRequest(BaseModel):
    """使用临时配置对某个通知通道进行连通性测试的请求载荷。"""

    channel: NotificationTestChannel  # 通知渠道
    items: List[SystemConfigUpdateItem] = Field(default_factory=list)  # 临时配置项
    mask_token: str = "******"  # 脱敏令牌
    title: str = Field(default="DSA 通知测试", min_length=1, max_length=80)  # 测试消息标题
    content: str = Field(default="这是一条来自 DSA Web 设置页的通知测试消息。", min_length=1, max_length=1000)  # 测试消息内容
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)  # 超时时间（秒）


class TestNotificationChannelResponse(BaseModel):
    """通知通道连通性测试响应。"""

    success: bool  # 是否成功
    message: str  # 结果消息
    error_code: Optional[str] = None  # 错误代码
    stage: Optional[str] = None  # 测试阶段
    retryable: bool = False  # 是否可重试
    latency_ms: Optional[int] = None  # 延迟（毫秒）
    attempts: List[NotificationTestAttempt] = Field(default_factory=list)  # 投递尝试列表


class DiscoverLLMChannelModelsRequest(BaseModel):
    """在不保存的前提下探测某 LLM 通道可用模型列表的请求载荷。"""

    name: str = "channel"  # 通道名称
    protocol: str = "openai"  # 协议类型
    base_url: str = ""  # 基础 URL
    api_key: str = ""  # API 密钥
    models: List[str] = Field(default_factory=list)  # 已知模型列表
    timeout_seconds: float = 20.0  # 超时时间（秒）


class DiscoverLLMChannelModelsResponse(BaseModel):
    """LLM 通道可用模型探测结果响应。"""

    success: bool  # 是否成功
    message: str  # 结果消息
    error: Optional[str] = None  # 错误信息
    error_code: Optional[str] = None  # 错误代码
    stage: Optional[str] = None  # 测试阶段
    retryable: Optional[bool] = None  # 是否可重试
    details: Dict[str, Any] = Field(default_factory=dict)  # 详细信息
    resolved_protocol: Optional[str] = None  # 解析后的协议
    models: List[str] = Field(default_factory=list)  # 可用模型列表
    latency_ms: Optional[int] = None  # 延迟（毫秒）


class SystemConfigValidationErrorResponse(BaseModel):
    """配置更新校验失败时的错误响应。"""

    error: str  # 错误类型
    message: str  # 错误消息
    issues: List[ConfigValidationIssue]  # 校验问题列表


class SystemConfigConflictResponse(BaseModel):
    """乐观锁版本冲突时的错误响应。"""

    error: str  # 错误类型
    message: str  # 错误消息
    current_config_version: str  # 当前配置版本号
