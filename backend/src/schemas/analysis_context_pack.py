# -*- coding: utf-8 -*-
"""内部 :class:`AnalysisContextPack` 数据契约（对应 Issue #1389 P1）。

这是 agent 分析流水线在 LLM 之前预装的"上下文信封"，所有字段都带
``status`` 以表达数据是否真实可用，便于 LLM / 后续 Agent 知道哪些证据
可以信赖、哪些是 fallback / missing / stale。

关键设计：

- **版本号契约** ``pack_version``：当前固定为 ``"1.0"``，由 ``Literal`` 限定。
- **质量状态机** :class:`ContextFieldStatus`：覆盖 available / missing /
  not_supported / fallback / stale / estimated / partial / fetch_failed。
- **字段级脱敏**：``to_safe_dict`` 在输出前调用 :func:`redact_sensitive_mapping`
  删除 token / apikey 等敏感映射字段。
- **冻结拷贝时仍校验版本号** ``model_copy``：不允许绕过 P1 契约把版本号改写。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from src.utils.sanitize import redact_sensitive_mapping


# 当前对外契约版本号；升级时同步修改 Literal 与适配器
PACK_VERSION = "1.0"
# 复用的 Literal 校验器，避免在多处重复声明字面量
_PACK_VERSION_ADAPTER = TypeAdapter(Literal["1.0"])


class _AnalysisContextModel(BaseModel):
    """P1 契约基础模型：启用 ``validate_assignment`` 让属性赋值也走校验。"""

    # 即使是 model.field = ... 也触发验证，避免脏数据写入
    model_config = ConfigDict(validate_assignment=True)


def _validate_iso8601_timestamp(value: Optional[str]) -> Optional[str]:
    """校验时间戳字符串为合法的 ISO 8601 格式（兼容 ``...Z`` 后缀）。

    Args:
        value: 形如 ``2024-01-01T00:00:00Z`` 或 ``2024-01-01T00:00:00+00:00`` 的字符串。

    Returns:
        Optional[str]: 原值返回；为 ``None`` 时也直接返回 ``None``。

    Raises:
        ValueError: 缺少 ``T`` 分隔符或解析失败时抛出。
    """
    if value is None:
        return value
    # 必须带 T 才是 ISO 8601 的日期时间形式
    if "T" not in value:
        raise ValueError("timestamp must be an ISO 8601 datetime string")
    # Python 原生 fromisoformat 不识别 Z 后缀，先做归一化
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("timestamp must be an ISO 8601 datetime string") from exc
    return value


class ContextFieldStatus(str, Enum):
    """字段 / 块级的"质量状态"枚举。

    - ``AVAILABLE``：直接获取到真实数据；
    - ``MISSING``：上游根本没有该字段；
    - ``NOT_SUPPORTED``：当前数据源不支持此字段；
    - ``FALLBACK``：用了兜底值（可在 ``fallback_from`` 追溯）；
    - ``STALE``：数据存在但已过期；
    - ``ESTIMATED``：根据上下文推算得到；
    - ``PARTIAL``：只有部分子字段可用；
    - ``FETCH_FAILED``：拉取失败（异常）。
    """

    AVAILABLE = "available"
    MISSING = "missing"
    NOT_SUPPORTED = "not_supported"
    FALLBACK = "fallback"
    STALE = "stale"
    ESTIMATED = "estimated"
    PARTIAL = "partial"
    FETCH_FAILED = "fetch_failed"


class AnalysisSubject(_AnalysisContextModel):
    """分析对象的最小身份槽位：必填代码 + 可选名称 / 市场。"""

    code: str
    stock_name: Optional[str] = None
    market: Optional[str] = None


class AnalysisContextItem(_AnalysisContextModel):
    """字段级的"单条输入上下文"。

    每个字段都用一个 Item 包裹，附 ``status`` / ``source`` / ``timestamp``
    等元信息，方便 LLM 区分"可信证据"和"补全猜测"。
    """

    # 必填：状态（枚举见 :class:`ContextFieldStatus`）
    status: ContextFieldStatus
    # 字段值；可为 None 表示"缺失"
    value: Optional[Any] = None
    # 数据来源标识（如 akshare / tushare / manual）
    source: Optional[str] = None
    # 数据时间戳，必须是 ISO 8601 字符串
    timestamp: Optional[str] = None
    # 若 status 是 FALLBACK，记录"原本想用但失败的那个源"
    fallback_from: Optional[str] = None
    # 若 status 是 MISSING，简要说明原因
    missing_reason: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_iso8601(cls, value: Optional[str]) -> Optional[str]:
        """时间戳字段必须符合 ISO 8601。"""
        return _validate_iso8601_timestamp(value)


class AnalysisContextBlock(_AnalysisContextModel):
    """块级分组：把多个相关 :class:`AnalysisContextItem` 聚合在一起。"""

    # 块的聚合状态，一般由内部 items 推导
    status: ContextFieldStatus
    # 子字段集合：key 为字段名，value 为该字段的 :class:`AnalysisContextItem`
    items: Dict[str, AnalysisContextItem] = Field(default_factory=dict)
    source: Optional[str] = None
    timestamp: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_iso8601(cls, value: Optional[str]) -> Optional[str]:
        """块级时间戳字段同样要求 ISO 8601。"""
        return _validate_iso8601_timestamp(value)


class DataQuality(_AnalysisContextModel):
    """整包的低敏感度数据质量汇总，用于在 UI / 日志中提示用户。"""

    # 总分 0-100；100 表示所有块都 AVAILABLE 且无 warnings
    overall_score: Optional[int] = Field(None, ge=0, le=100)
    # 等级定性标签
    level: Optional[Literal["good", "usable", "limited", "poor"]] = None
    # 块名 → 块分；用于细粒度排错
    block_scores: Dict[str, int] = Field(default_factory=dict)
    limitations: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AnalysisContextPack(_AnalysisContextModel):
    """带版本号的内部分析输入信封。

    顶层结构由 ``subject`` / ``pack_version`` / ``phase`` / ``blocks`` /
    ``data_quality`` / ``metadata`` / ``created_at`` 组成，其中 ``pack_version``
    锁死为 ``"1.0"``，升级需要做兼容层。
    """

    subject: AnalysisSubject
    pack_version: Literal["1.0"] = PACK_VERSION
    phase: Optional[Dict[str, Any]] = None
    blocks: Dict[str, AnalysisContextBlock] = Field(default_factory=dict)
    data_quality: DataQuality = Field(default_factory=DataQuality)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # 默认创建时间为 UTC，避免跨时区混乱
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_safe_dict(self) -> Dict[str, Any]:
        """返回 JSON 安全的字典，敏感映射值已脱敏。

        会先 ``model_dump(mode="json")`` 把 datetime 转成 ISO 字符串，
        再走 :func:`redact_sensitive_mapping` 删除 ``token`` / ``api_key``
        等字段。
        """
        return redact_sensitive_mapping(self.model_dump(mode="json"))

    def model_copy(
        self,
        *,
        update: Optional[Mapping[str, Any]] = None,
        deep: bool = False,
    ) -> "AnalysisContextPack":
        """复制 pack，但不绕过固定的 P1 契约字段。

        复制时若试图在 ``update`` 中传入 ``pack_version``，会先用
        ``_PACK_VERSION_ADAPTER`` 校验合法性——版本号必须仍在 Literal
        允许的取值集合内，避免下游误用旧版本数据。
        """
        if update is not None and "pack_version" in update:
            # 复制时若试图改写版本号，先用适配器校验合法性
            _PACK_VERSION_ADAPTER.validate_python(update["pack_version"])
        return super().model_copy(update=update, deep=deep)
