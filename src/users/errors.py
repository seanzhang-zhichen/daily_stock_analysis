# -*- coding: utf-8 -*-
"""To C 用户体系统一业务错误。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UserErrorCode(str, Enum):
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
    """业务异常: 由 service 层抛出, endpoint 层翻译为 HTTP 响应。"""

    code: UserErrorCode
    message: str

    def __str__(self) -> str:  # noqa: D105
        return f"{self.code.value}: {self.message}"
