# -*- coding: utf-8 -*-
"""密码哈希与强度校验工具。

格式: ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``。

之所以沿用 :mod:`hashlib` 而不是 ``bcrypt`` / ``argon2``, 是为了避免给项目
增加底层 C 依赖（仓库目前依赖 ``hashlib``、``hmac`` 已可满足需求且与
``src.auth`` 一致）。后续若需要更强算法, 可在此模块替换实现而不影响调用方。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Optional

PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16
HASH_BYTES = 32
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(value: str) -> bool:
    """简单邮箱合法性校验, 不做严格 RFC5322。"""
    if not value:
        return False
    value = value.strip()
    if len(value) > 254:
        return False
    return bool(_EMAIL_RE.match(value))


def validate_password_strength(value: str) -> Optional[str]:
    """返回错误描述, 通过返回 ``None``。"""
    if not value:
        return "密码不能为空"
    if len(value) < MIN_PASSWORD_LEN:
        return f"密码至少 {MIN_PASSWORD_LEN} 位"
    if len(value) > MAX_PASSWORD_LEN:
        return f"密码长度不能超过 {MAX_PASSWORD_LEN} 位"
    return None


def hash_password(password: str) -> str:
    """对密码进行 PBKDF2-SHA256 哈希, 返回可直接落库的字符串。"""
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=HASH_BYTES,
    )
    return "pbkdf2_sha256${iter}${salt}${hash}".format(
        iter=PBKDF2_ITERATIONS,
        salt=base64.standard_b64encode(salt).decode("ascii"),
        hash=base64.standard_b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """验证密码是否与持久化字符串匹配。"""
    if not stored or not password:
        return False
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iterations = int(parts[1])
        salt = base64.standard_b64decode(parts[2])
        expected = base64.standard_b64decode(parts[3])
    except (ValueError, TypeError):
        return False
    if iterations <= 0 or len(salt) == 0 or len(expected) == 0:
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=len(expected),
    )
    return hmac.compare_digest(derived, expected)


def hash_token(token: str) -> str:
    """对一次性 token / session token 计算 SHA-256 哈希用于落库索引。"""
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
