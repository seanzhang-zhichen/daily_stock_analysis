# -*- coding: utf-8 -*-
"""一键退订 token (Phase 3)。

每日推送邮件需要在底部携带一个 ``一键退订`` 链接, 点击后即可关闭 ``email_enabled``
或 ``daily_push_enabled`` 偏好。本模块提供 **无状态** 的 HMAC token 生成与校验:

- token = base64url(payload) + "." + base64url(hmac_sha256(secret, payload))
- payload = ``"{user_id}:{action}:{issued_at_unix}"``
- ``action`` 当前支持 ``daily`` / ``email`` 两种, 默认按 ``daily`` 关闭每日推送开关。

无状态意味着不依赖额外的 DB 表 (避免再加一张过期清理表), 通过 ``issued_at``
+ 服务端常量 ``UNSUBSCRIBE_TTL_SECONDS`` 控制有效期 (默认 90 天)。
密钥优先取 ``UNSUBSCRIBE_SIGNING_KEY``, 缺失时回退到 ``ADMIN_API_SECRET``,
两者都缺失时仍会启用一段固定 fallback 仅供本地开发,
生产环境必须显式配置以保证 token 不可伪造。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# 90 天; 邮件可能被用户延后翻阅, 但太久会增加重放风险。
UNSUBSCRIBE_TTL_SECONDS = 90 * 24 * 3600

ACTION_DAILY = "daily"      # 关闭每日推送
ACTION_EMAIL = "email"      # 关闭所有邮件
ALLOWED_ACTIONS = (ACTION_DAILY, ACTION_EMAIL)


def _load_signing_key() -> bytes:
    """按优先级返回签名密钥。

    生产环境应显式配置 ``UNSUBSCRIBE_SIGNING_KEY``; 其它兜底仅为开发便利。
    """
    for env_name in ("UNSUBSCRIBE_SIGNING_KEY", "ADMIN_API_SECRET"):
        raw = (os.getenv(env_name) or "").strip()
        if raw:
            return raw.encode("utf-8")
    logger.warning(
        "UNSUBSCRIBE_SIGNING_KEY / ADMIN_API_SECRET 均未配置, "
        "退订 token 使用本地开发默认密钥, 生产部署必须显式配置。"
    )
    return b"dsa-unsubscribe-dev-key"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = 4 - (len(value) % 4)
    if padding and padding < 4:
        value = value + ("=" * padding)
    return base64.urlsafe_b64decode(value.encode("ascii"))


def build_unsubscribe_token(
    *,
    user_id: int,
    action: str = ACTION_DAILY,
    issued_at: Optional[int] = None,
) -> str:
    """签发一个退订 token。"""
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported unsubscribe action: {action}")
    issued_at = int(issued_at if issued_at is not None else time.time())
    payload = f"{int(user_id)}:{action}:{issued_at}".encode("utf-8")
    sig = hmac.new(_load_signing_key(), payload, hashlib.sha256).digest()
    return f"{_b64url_encode(payload)}.{_b64url_encode(sig)}"


@dataclass(frozen=True)
class UnsubscribeClaim:
    user_id: int
    action: str
    issued_at: int


def verify_unsubscribe_token(
    token: str,
    *,
    ttl_seconds: int = UNSUBSCRIBE_TTL_SECONDS,
    now: Optional[int] = None,
) -> Optional[UnsubscribeClaim]:
    """校验 token; 失败返回 ``None``。

    失败原因有三种, 均合并为 ``None``:
    - 格式不对 / base64 / 数值解析失败
    - 签名校验失败
    - 已超过 ``ttl_seconds``
    """
    if not token or not isinstance(token, str):
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except Exception:
        return None

    expected = hmac.new(_load_signing_key(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        return None

    try:
        user_str, action, issued_str = payload.decode("utf-8").split(":", 2)
        user_id = int(user_str)
        issued_at = int(issued_str)
    except Exception:
        return None

    if action not in ALLOWED_ACTIONS:
        return None

    now = int(now if now is not None else time.time())
    if now - issued_at > ttl_seconds:
        return None

    return UnsubscribeClaim(user_id=user_id, action=action, issued_at=issued_at)


def get_public_base_url() -> str:
    """Return the public API base URL without a trailing slash."""
    raw = (os.getenv("USER_PUBLIC_BASE_URL") or "").strip()
    if raw:
        return raw.rstrip("/")
    return "http://localhost:8000"


def get_frontend_public_base_url() -> str:
    raw = (os.getenv("USER_FRONTEND_BASE_URL") or "").strip()
    if raw:
        return raw.rstrip("/")
    return "http://localhost:5200"


def build_unsubscribe_url(
    *,
    user_id: int,
    action: str = ACTION_DAILY,
    base_url: Optional[str] = None,
    issued_at: Optional[int] = None,
) -> str:
    """根据用户 ID + action 拼接完整的退订 URL。"""
    token = build_unsubscribe_token(user_id=user_id, action=action, issued_at=issued_at)
    base = (base_url or get_public_base_url()).rstrip("/")
    return (
        f"{base}/api/v1/account/notification-prefs/unsubscribe"
        f"?token={quote(token, safe='')}"
    )
