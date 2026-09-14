# -*- coding: utf-8 -*-
"""To C 用户体系统一业务错误。

该模块定义了用户体系对外暴露的稳定错误码 :class:`UserErrorCode` 和
业务异常 :class:`UserError`，用于在 service 层与 endpoint 层之间传递错误信息。

使用方式：
    raise UserError(UserErrorCode.INVALID_CREDENTIALS, "邮箱或密码错误")

endpoint 层通过捕获 UserError 并翻译为对应的 HTTP 响应：
    - 客户端错误（4xx）→ 返回 400/401/403/404/429 等
    - 服务端错误（5xx）→ 返回 500
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UserErrorCode(str, Enum):
    """用户体系对外暴露的稳定错误码。

    每个错误码对应一个具体的业务场景，便于前端根据错误码做差异化处理：
    - 注册相关：REGISTRATION_DISABLED, EMAIL_ALREADY_REGISTERED, INVITE_CODE_REQUIRED, INVITE_CODE_INVALID
    - 认证相关：INVALID_EMAIL, INVALID_PASSWORD, PASSWORD_MISMATCH, INVALID_CREDENTIALS, EMAIL_NOT_VERIFIED
    - 状态相关：USER_DISABLED, INVALID_TOKEN, TOKEN_EXPIRED
    - 限流相关：RATE_LIMITED, QUOTA_EXCEEDED
    - 通用：VALIDATION_ERROR, NOT_FOUND, PERMISSION_DENIED
    """

    REGISTRATION_DISABLED = "registration_disabled"
    INVALID_EMAIL = "invalid_email"
    INVALID_PASSWORD = "invalid_password"
    PASSWORD_MISMATCH = "password_mismatch"
    EMAIL_ALREADY_REGISTERED = "email_already_registered"
    INVALID_CREDENTIALS = "invalid_credentials"
    EMAIL_NOT_VERIFIED = "email_not_verified"
    USER_DISABLED = "user_disabled"
    INVALID_TOKEN = "invalid_token"
    TOKEN_EXPIRED = "token_expired"
    INVITE_CODE_REQUIRED = "invite_code_required"
    INVITE_CODE_INVALID = "invite_code_invalid"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"


@dataclass(frozen=True)
class UserError(Exception):
    """业务异常: 由 service 层抛出, endpoint 层翻译为 HTTP 响应。

    属性:
        code: 错误码，类型为 :class:`UserErrorCode`
        message: 人类可读的错误描述

    示例:
        >>> raise UserError(UserErrorCode.INVALID_CREDENTIALS, "邮箱或密码错误")
    """

    code: UserErrorCode
    message: str

    def __str__(self) -> str:  # noqa: D105
        """返回便于日志阅读的 code/message 组合。"""
        return f"{self.code.value}: {self.message}"
