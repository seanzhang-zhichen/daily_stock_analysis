# -*- coding: utf-8 -*-
"""To C 用户体系相关的环境变量解析。

所有读取都基于 :func:`os.getenv` + :func:`src.config.parse_env_bool` /
:func:`src.config.parse_env_int`，保持与项目其它配置的解析语义一致。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from src.config import parse_env_bool, parse_env_int


SESSION_COOKIE_NAME = "dsa_user_session"
DEFAULT_SESSION_TTL_HOURS = 24 * 14  # 14 天
DEFAULT_VERIFICATION_TTL_HOURS = 24
DEFAULT_RESET_TTL_HOURS = 2
DEFAULT_FREE_PLAN_DAILY_ANALYSIS = 5
DEFAULT_FREE_PLAN_DAILY_AGENT = 5
DEFAULT_FREE_PLAN_MAX_STOCKS = 3
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
    free_daily_analysis: int
    free_daily_agent: int
    free_max_stocks: int
    invite_codes: tuple[str, ...]
    register_disposable_block: bool
    register_ip_daily_max: int
    register_email_daily_max: int
    register_rate_window_hours: int
    register_mx_check_enabled: bool


def _parse_invite_codes(raw: Optional[str]) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(code.strip() for code in raw.split(",") if code.strip())


def load_user_mode_settings() -> UserModeSettings:
    """读取 To C 用户体系相关的环境变量并返回解析后的快照。"""

    enabled = True
    public_registration = parse_env_bool(
        os.getenv("USER_PUBLIC_REGISTRATION_ENABLED"),
        default=enabled,
    )
    session_ttl = parse_env_int(
        os.getenv("USER_SESSION_TTL_HOURS"),
        DEFAULT_SESSION_TTL_HOURS,
        field_name="USER_SESSION_TTL_HOURS",
        minimum=1,
    )
    verify_ttl = parse_env_int(
        os.getenv("USER_VERIFICATION_TTL_HOURS"),
        DEFAULT_VERIFICATION_TTL_HOURS,
        field_name="USER_VERIFICATION_TTL_HOURS",
        minimum=1,
    )
    reset_ttl = parse_env_int(
        os.getenv("USER_RESET_TTL_HOURS"),
        DEFAULT_RESET_TTL_HOURS,
        field_name="USER_RESET_TTL_HOURS",
        minimum=1,
    )

    free_analysis = parse_env_int(
        os.getenv("USER_FREE_DAILY_ANALYSIS"),
        DEFAULT_FREE_PLAN_DAILY_ANALYSIS,
        field_name="USER_FREE_DAILY_ANALYSIS",
        minimum=0,
    )
    free_agent = parse_env_int(
        os.getenv("USER_FREE_DAILY_AGENT"),
        DEFAULT_FREE_PLAN_DAILY_AGENT,
        field_name="USER_FREE_DAILY_AGENT",
        minimum=0,
    )
    free_max_stocks = parse_env_int(
        os.getenv("USER_FREE_MAX_STOCKS"),
        DEFAULT_FREE_PLAN_MAX_STOCKS,
        field_name="USER_FREE_MAX_STOCKS",
        minimum=0,
    )

    register_disposable_block = parse_env_bool(
        os.getenv("USER_REGISTER_DISPOSABLE_BLOCK"),
        default=True,
    )
    register_ip_daily_max = parse_env_int(
        os.getenv("USER_REGISTER_IP_DAILY_MAX"),
        DEFAULT_REGISTER_IP_DAILY_MAX,
        field_name="USER_REGISTER_IP_DAILY_MAX",
        minimum=0,
    )
    register_email_daily_max = parse_env_int(
        os.getenv("USER_REGISTER_EMAIL_DAILY_MAX"),
        DEFAULT_REGISTER_EMAIL_DAILY_MAX,
        field_name="USER_REGISTER_EMAIL_DAILY_MAX",
        minimum=0,
    )
    register_rate_window_hours = parse_env_int(
        os.getenv("USER_REGISTER_RATE_WINDOW_HOURS"),
        DEFAULT_REGISTER_RATE_WINDOW_HOURS,
        field_name="USER_REGISTER_RATE_WINDOW_HOURS",
        minimum=1,
    )

    return UserModeSettings(
        enabled=enabled,
        public_registration_enabled=public_registration,
        require_email_verification=True,
        session_ttl_hours=session_ttl,
        verification_ttl_hours=verify_ttl,
        reset_ttl_hours=reset_ttl,
        free_daily_analysis=free_analysis,
        free_daily_agent=free_agent,
        free_max_stocks=free_max_stocks,
        invite_codes=_parse_invite_codes(os.getenv("USER_INVITE_CODES")),
        register_disposable_block=register_disposable_block,
        register_ip_daily_max=register_ip_daily_max,
        register_email_daily_max=register_email_daily_max,
        register_rate_window_hours=register_rate_window_hours,
        register_mx_check_enabled=parse_env_bool(
            os.getenv("USER_EMAIL_MX_CHECK_ENABLED"),
            default=False,
        ),
    )


def is_user_mode_enabled() -> bool:
    """快捷判断: 是否启用 To C 多用户模式。"""
    return True
