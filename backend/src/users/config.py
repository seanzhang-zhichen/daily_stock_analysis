# -*- coding: utf-8 -*-
"""To C 用户体系相关的运行期配置解析。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.config import parse_env_int
from src.users.consents import CURRENT_TERMS_VERSION
from src.users.platform_settings import get_platform_setting_value


SESSION_COOKIE_NAME = "dsa_user_session"
DEFAULT_SESSION_TTL_HOURS = 24 * 14  # 14 天
DEFAULT_VERIFICATION_TTL_HOURS = 24
DEFAULT_RESET_TTL_HOURS = 2
DEFAULT_REGISTER_IP_DAILY_MAX = 10
DEFAULT_REGISTER_EMAIL_DAILY_MAX = 3
DEFAULT_REGISTER_RATE_WINDOW_HOURS = 24


@dataclass(frozen=True)
class UserModeSettings:
    """运行期解析过的 To C 模块配置。"""

    enabled: bool
    public_registration_enabled: bool
    require_email_verification: bool
    session_ttl_hours: int
    verification_ttl_hours: int
    reset_ttl_hours: int
    invite_codes: tuple[str, ...]
    register_disposable_block: bool
    register_ip_daily_max: int
    register_email_daily_max: int
    register_rate_window_hours: int
    register_mx_check_enabled: bool
    disposable_email_domains: tuple[str, ...] = ()
    disposable_email_domains_replace: bool = False
    terms_version: str = CURRENT_TERMS_VERSION


def _parse_invite_codes(raw: Optional[str]) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(code.strip() for code in raw.split(",") if code.strip())


def _setting(db, key: str):
    return get_platform_setting_value(db, key)


def load_user_mode_settings(db=None) -> UserModeSettings:
    """读取 To C 用户体系相关的环境变量并返回解析后的快照。"""

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
