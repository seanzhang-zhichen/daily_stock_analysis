# -*- coding: utf-8 -*-
"""To C 用户体系相关的运行期配置解析。

该模块负责从环境变量和平台配置中读取 To C 用户体系的运行时参数，
并提供结构化的 :class:`UserModeSettings` 对象供上层使用。
所有配置项均有默认值，未配置时不会导致启动失败。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.config import parse_env_int
from src.users.consents import CURRENT_TERMS_VERSION
from src.users.platform_settings import get_platform_setting_value


# Session cookie 名称，用于浏览器端识别用户会话
SESSION_COOKIE_NAME = "dsa_user_session"
# 默认会话有效期：14 天（两周）
DEFAULT_SESSION_TTL_HOURS = 24 * 14
# 默认邮箱验证链接有效期：24 小时
DEFAULT_VERIFICATION_TTL_HOURS = 24
# 默认密码重置链接有效期：2 小时
DEFAULT_RESET_TTL_HOURS = 2
# 默认同一 IP 每日最大注册次数
DEFAULT_REGISTER_IP_DAILY_MAX = 10
# 默认同一邮箱每日最大注册次数
DEFAULT_REGISTER_EMAIL_DAILY_MAX = 10
# 默认注册频率限制窗口：1 小时
DEFAULT_REGISTER_RATE_WINDOW_HOURS = 1


@dataclass(frozen=True)
class UserModeSettings:
    """运行期解析过的 To C 模块配置。

    所有字段均为不可变对象，创建后不可修改，保证配置快照的一致性。
    """

    enabled: bool  # To C 用户模式是否启用（当前版本恒为 True）
    public_registration_enabled: bool  # 是否允许公开注册（无需邀请码）
    require_email_verification: bool  # 注册时是否强制要求邮箱验证
    session_ttl_hours: int  # 会话有效期（小时）
    verification_ttl_hours: int  # 邮箱验证链接有效期（小时）
    reset_ttl_hours: int  # 密码重置链接有效期（小时）
    invite_codes: tuple[str, ...]  # 有效的邀请码列表
    register_disposable_block: bool  # 是否拦截一次性邮箱注册
    register_ip_daily_max: int  # 同一 IP 每日最大注册次数
    register_email_daily_max: int  # 同一邮箱每日最大注册次数
    register_rate_window_hours: int  # 注册频率限制窗口（小时）
    register_mx_check_enabled: bool  # 注册时是否启用 MX 记录检查
    disposable_email_domains: tuple[str, ...] = ()  # 已知的一次性邮箱域名列表
    disposable_email_domains_replace: bool = False  # 是否替换默认的一次性邮箱域名列表
    terms_version: str = CURRENT_TERMS_VERSION  # 当前生效的服务条款版本


def _parse_invite_codes(raw: Optional[str]) -> tuple[str, ...]:
    """解析逗号分隔的邀请码配置，自动去掉空项。

    示例：
        >>> _parse_invite_codes("CODE1, CODE2, , CODE3")
        ('CODE1', 'CODE2', 'CODE3')
    """
    if not raw:
        return ()
    return tuple(code.strip() for code in raw.split(",") if code.strip())


def _setting(db, key: str):
    """读取平台配置值，允许数据库配置覆盖默认环境语义。

    封装对 :func:`get_platform_setting_value` 的调用，便于统一处理
    配置读取逻辑和后续可能的缓存策略。
    """
    return get_platform_setting_value(db, key)


def load_user_mode_settings(db=None) -> UserModeSettings:
    """读取 To C 用户体系相关的环境变量并返回解析后的快照。

    配置优先级（从高到低）：
    1. 数据库平台配置（通过 _setting 读取）
    2. 环境变量
    3. 模块默认值（以 DEFAULT_ 开头的常量）

    Args:
        db: 数据库会话对象，用于读取平台配置。为 None 时仅使用环境变量和默认值。

    Returns:
        :class:`UserModeSettings` 配置快照对象。
    """

    enabled = True
    public_registration = bool(_setting(db, "USER_PUBLIC_REGISTRATION_ENABLED"))
    session_ttl = parse_env_int(
        str(_setting(db, "USER_SESSION_TTL_HOURS")),
        DEFAULT_SESSION_TTL_HOURS,
        field_name="USER_SESSION_TTL_HOURS",
        minimum=1,
    )
    verify_ttl = parse_env_int(
        str(_setting(db, "USER_VERIFICATION_TTL_HOURS")),
        DEFAULT_VERIFICATION_TTL_HOURS,
        field_name="USER_VERIFICATION_TTL_HOURS",
        minimum=1,
    )
    reset_ttl = parse_env_int(
        str(_setting(db, "USER_RESET_TTL_HOURS")),
        DEFAULT_RESET_TTL_HOURS,
        field_name="USER_RESET_TTL_HOURS",
        minimum=1,
    )

    register_disposable_block = bool(_setting(db, "USER_REGISTER_DISPOSABLE_BLOCK"))
    register_ip_daily_max = parse_env_int(
        str(_setting(db, "USER_REGISTER_IP_DAILY_MAX")),
        DEFAULT_REGISTER_IP_DAILY_MAX,
        field_name="USER_REGISTER_IP_DAILY_MAX",
        minimum=0,
    )
    register_email_daily_max = parse_env_int(
        str(_setting(db, "USER_REGISTER_EMAIL_DAILY_MAX")),
        DEFAULT_REGISTER_EMAIL_DAILY_MAX,
        field_name="USER_REGISTER_EMAIL_DAILY_MAX",
        minimum=0,
    )
    register_rate_window_hours = parse_env_int(
        str(_setting(db, "USER_REGISTER_RATE_WINDOW_HOURS")),
        DEFAULT_REGISTER_RATE_WINDOW_HOURS,
        field_name="USER_REGISTER_RATE_WINDOW_HOURS",
        minimum=1,
    )
    disposable_domains = _parse_invite_codes(str(_setting(db, "USER_DISPOSABLE_EMAIL_DOMAINS")).lower())

    return UserModeSettings(
        enabled=enabled,
        public_registration_enabled=public_registration,
        require_email_verification=True,
        session_ttl_hours=session_ttl,
        verification_ttl_hours=verify_ttl,
        reset_ttl_hours=reset_ttl,
        invite_codes=_parse_invite_codes(str(_setting(db, "USER_INVITE_CODES"))),
        register_disposable_block=register_disposable_block,
        register_ip_daily_max=register_ip_daily_max,
        register_email_daily_max=register_email_daily_max,
        register_rate_window_hours=register_rate_window_hours,
        register_mx_check_enabled=bool(_setting(db, "USER_EMAIL_MX_CHECK_ENABLED")),
        disposable_email_domains=disposable_domains,
        disposable_email_domains_replace=bool(_setting(db, "USER_DISPOSABLE_EMAIL_DOMAINS_REPLACE")),
        terms_version=str(_setting(db, "USER_TERMS_VERSION")).strip() or CURRENT_TERMS_VERSION,
    )


def is_user_mode_enabled() -> bool:
    """快捷判断: 是否启用 To C 多用户模式。"""
    return True
