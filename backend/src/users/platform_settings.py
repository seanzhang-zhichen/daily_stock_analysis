# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.config import parse_env_bool, parse_env_int
from src.storage.models.app import AppPlatformSetting
from src.users.consents import CURRENT_TERMS_VERSION

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlatformSettingDefinition:
    key: str
    title: str
    description: str
    category: str
    value_type: str
    default_value: str
    env_name: Optional[str] = None
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    max_length: Optional[int] = None
    multiline: bool = False


_PLATFORM_SETTING_DEFINITIONS: tuple[PlatformSettingDefinition, ...] = (
    PlatformSettingDefinition(
        key="USER_PUBLIC_REGISTRATION_ENABLED",
        title="开放公开注册",
        description="关闭后新用户无法直接注册，可配合邀请码控制内测范围。",
        category="registration",
        value_type="boolean",
        default_value="true",
    ),
    PlatformSettingDefinition(
        key="USER_INVITE_CODES",
        title="注册邀请码",
        description="逗号分隔；留空表示注册时不要求邀请码。",
        category="registration",
        value_type="string",
        default_value="",
        max_length=2048,
        multiline=True,
    ),
    PlatformSettingDefinition(
        key="USER_SESSION_TTL_HOURS",
        title="登录有效期（小时）",
        description="影响新登录用户的 Cookie 有效期。",
        category="registration",
        value_type="integer",
        default_value="336",
        minimum=1,
        maximum=8760,
    ),
    PlatformSettingDefinition(
        key="USER_VERIFICATION_TTL_HOURS",
        title="邮箱验证有效期（小时）",
        description="影响新生成的邮箱验证链接有效期。",
        category="registration",
        value_type="integer",
        default_value="24",
        minimum=1,
        maximum=168,
    ),
    PlatformSettingDefinition(
        key="USER_RESET_TTL_HOURS",
        title="重置密码有效期（小时）",
        description="影响新生成的密码重置 token 有效期。",
        category="registration",
        value_type="integer",
        default_value="2",
        minimum=1,
        maximum=72,
    ),
    PlatformSettingDefinition(
        key="USER_REGISTER_DISPOSABLE_BLOCK",
        title="拦截一次性邮箱",
        description="开启后注册会拒绝常见临时邮箱域名。",
        category="risk_control",
        value_type="boolean",
        default_value="true",
    ),
    PlatformSettingDefinition(
        key="USER_DISPOSABLE_EMAIL_DOMAINS",
        title="额外邮箱域名黑名单",
        description="逗号分隔；默认追加到内置一次性邮箱黑名单。",
        category="risk_control",
        value_type="string",
        default_value="",
        max_length=4096,
        multiline=True,
    ),
    PlatformSettingDefinition(
        key="USER_DISPOSABLE_EMAIL_DOMAINS_REPLACE",
        title="替换内置黑名单",
        description="开启后只使用上方自定义域名列表，不再追加内置黑名单。",
        category="risk_control",
        value_type="boolean",
        default_value="false",
    ),
    PlatformSettingDefinition(
        key="USER_REGISTER_IP_DAILY_MAX",
        title="IP 注册尝试上限",
        description="统计窗口内同一 IP 的最大注册尝试次数；0 表示不限制。",
        category="risk_control",
        value_type="integer",
        default_value="10",
        minimum=0,
        maximum=100000,
    ),
    PlatformSettingDefinition(
        key="USER_REGISTER_EMAIL_DAILY_MAX",
        title="邮箱注册尝试上限",
        description="统计窗口内同一邮箱的最大注册尝试次数；0 表示不限制。",
        category="risk_control",
        value_type="integer",
        default_value="3",
        minimum=0,
        maximum=100000,
    ),
    PlatformSettingDefinition(
        key="USER_REGISTER_RATE_WINDOW_HOURS",
        title="注册限流窗口（小时）",
        description="IP 与邮箱注册尝试统计窗口。",
        category="risk_control",
        value_type="integer",
        default_value="24",
        minimum=1,
        maximum=720,
    ),
    PlatformSettingDefinition(
        key="USER_EMAIL_MX_CHECK_ENABLED",
        title="邮箱域名解析校验",
        description="开启后注册时会校验邮箱域名是否可解析；网络异常时放行。",
        category="risk_control",
        value_type="boolean",
        default_value="false",
    ),
    PlatformSettingDefinition(
        key="PAYMENT_ENABLED",
        title="启用真实支付通道",
        description="开启后调用已部署配置的微信/支付宝通道；密钥与证书仍从环境变量读取。",
        category="payment",
        value_type="boolean",
        default_value="false",
    ),
    PlatformSettingDefinition(
        key="ORDER_EXPIRE_MINUTES",
        title="订单支付超时时间（分钟）",
        description="新创建订单的有效期；已创建订单不受后续修改影响。",
        category="payment",
        value_type="integer",
        default_value="15",
        minimum=1,
        maximum=1440,
    ),
    PlatformSettingDefinition(
        key="USER_TERMS_VERSION",
        title="用户协议版本",
        description="变更后未接受该版本的用户会被标记为需要重新确认。",
        category="compliance",
        value_type="string",
        default_value=CURRENT_TERMS_VERSION,
        max_length=32,
    ),
)

_DEFINITIONS_BY_KEY = {item.key: item for item in _PLATFORM_SETTING_DEFINITIONS}
_CATEGORY_ORDER = {"registration": 10, "risk_control": 20, "payment": 30, "compliance": 40}
_FALSE_VALUES = {"0", "false", "no", "off"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


def get_platform_setting_definitions() -> tuple[PlatformSettingDefinition, ...]:
    return _PLATFORM_SETTING_DEFINITIONS


def _get_definition(key: str) -> PlatformSettingDefinition:
    normalized = (key or "").strip().upper()
    definition = _DEFINITIONS_BY_KEY.get(normalized)
    if definition is None:
        raise ValueError(f"未知平台配置项: {key}")
    return definition


def _get_row(db: Optional[Session], key: str) -> Optional[AppPlatformSetting]:
    if db is None:
        return None
    try:
        return db.query(AppPlatformSetting).filter(AppPlatformSetting.key == key).first()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取平台配置失败 key=%s: %s", key, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def _env_value(definition: PlatformSettingDefinition) -> Optional[str]:
    return os.getenv(definition.env_name or definition.key)


def get_platform_setting_raw_value(db: Optional[Session], key: str) -> tuple[str, str]:
    definition = _get_definition(key)
    row = _get_row(db, definition.key)
    if row is not None and row.value is not None:
        return str(row.value), "db"
    env_value = _env_value(definition)
    if env_value is not None and str(env_value).strip():
        return str(env_value), "env"
    return definition.default_value, "default"


def get_platform_setting_value(db: Optional[Session], key: str) -> Any:
    definition = _get_definition(key)
    raw_value, _ = get_platform_setting_raw_value(db, definition.key)
    if definition.value_type == "boolean":
        return parse_env_bool(raw_value, default=parse_env_bool(definition.default_value))
    if definition.value_type == "integer":
        return parse_env_int(
            raw_value,
            int(definition.default_value or 0),
            field_name=definition.key,
            minimum=definition.minimum,
            maximum=definition.maximum,
        )
    return str(raw_value or "")


def _normalize_csv(value: str, *, lowercase: bool = False) -> str:
    items: list[str] = []
    seen: set[str] = set()
    for item in str(value or "").replace("\n", ",").split(","):
        normalized = item.strip()
        if lowercase:
            normalized = normalized.lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            items.append(normalized)
    return ",".join(items)


def normalize_platform_setting_value(key: str, value: Any) -> str:
    definition = _get_definition(key)
    if definition.value_type == "boolean":
        if isinstance(value, bool):
            return "true" if value else "false"
        normalized = str(value or "").strip().lower()
        if normalized in _TRUE_VALUES:
            return "true"
        if normalized in _FALSE_VALUES:
            return "false"
        raise ValueError(f"{definition.title} 必须为布尔值")

    if definition.value_type == "integer":
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{definition.title} 必须为整数") from exc
        if definition.minimum is not None and parsed < definition.minimum:
            raise ValueError(f"{definition.title} 不能小于 {definition.minimum}")
        if definition.maximum is not None and parsed > definition.maximum:
            raise ValueError(f"{definition.title} 不能大于 {definition.maximum}")
        return str(parsed)

    normalized = str(value or "").strip()
    if definition.key == "USER_INVITE_CODES":
        normalized = _normalize_csv(normalized)
    elif definition.key == "USER_DISPOSABLE_EMAIL_DOMAINS":
        normalized = _normalize_csv(normalized, lowercase=True)
    elif definition.key == "USER_TERMS_VERSION" and not normalized:
        raise ValueError("用户协议版本不能为空")
    if definition.max_length is not None and len(normalized) > definition.max_length:
        raise ValueError(f"{definition.title} 长度不能超过 {definition.max_length} 个字符")
    return normalized


def _typed_value(definition: PlatformSettingDefinition, raw_value: str) -> Any:
    if definition.value_type == "boolean":
        return parse_env_bool(raw_value, default=parse_env_bool(definition.default_value))
    if definition.value_type == "integer":
        return parse_env_int(
            raw_value,
            int(definition.default_value or 0),
            field_name=definition.key,
            minimum=definition.minimum,
            maximum=definition.maximum,
        )
    return raw_value


def serialize_platform_settings(db: Optional[Session]) -> list[dict[str, Any]]:
    rows_by_key: dict[str, AppPlatformSetting] = {}
    if db is not None:
        try:
            rows_by_key = {row.key: row for row in db.query(AppPlatformSetting).all()}
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取平台配置列表失败: %s", exc)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass

    items: list[dict[str, Any]] = []
    for definition in sorted(
        _PLATFORM_SETTING_DEFINITIONS,
        key=lambda item: (_CATEGORY_ORDER.get(item.category, 999), item.key),
    ):
        row = rows_by_key.get(definition.key)
        if row is not None and row.value is not None:
            raw_value = str(row.value)
            source = "db"
        else:
            env_value = _env_value(definition)
            if env_value is not None and str(env_value).strip():
                raw_value = str(env_value)
                source = "env"
            else:
                raw_value = definition.default_value
                source = "default"
        items.append(
            {
                "key": definition.key,
                "title": definition.title,
                "description": definition.description,
                "category": definition.category,
                "valueType": definition.value_type,
                "value": _typed_value(definition, raw_value),
                "rawValue": raw_value,
                "defaultValue": _typed_value(definition, definition.default_value),
                "source": source,
                "minimum": definition.minimum,
                "maximum": definition.maximum,
                "maxLength": definition.max_length,
                "multiline": definition.multiline,
                "updatedAt": row.updated_at.isoformat() if row is not None and row.updated_at else None,
            }
        )
    return items


def upsert_platform_settings(
    db: Session,
    items: list[dict[str, Any]],
    *,
    admin_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    now = datetime.utcnow()
    for item in items:
        key = str(item.get("key") or "").strip().upper()
        value = normalize_platform_setting_value(key, item.get("value"))
        row = db.query(AppPlatformSetting).filter(AppPlatformSetting.key == key).first()
        if row is None:
            row = AppPlatformSetting(key=key, created_at=now)
        row.value = value
        row.updated_by = admin_id
        row.updated_at = now
        db.add(row)
    db.commit()
    return serialize_platform_settings(db)
