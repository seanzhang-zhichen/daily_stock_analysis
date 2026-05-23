# -*- coding: utf-8 -*-
"""C 端用户 session 管理。

设计:
- 客户端持有 ``dsa_user_session`` cookie, 内容为不透明随机 token。
- 服务端在 ``app_user_sessions`` 表中存 ``token_hash`` + ``expires_at`` +
  ``revoked_at``, 校验时比对 token 哈希。
- 登出 / 修改密码可对单条 session 写 ``revoked_at`` 立即吊销。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from src.storage import AppUser, AppUserSession
from src.users.passwords import hash_token


SESSION_TOKEN_BYTES = 32


@dataclass(frozen=True)
class IssuedSession:
    """登录成功后传给上层的载荷。"""

    user: AppUser
    cookie_value: str
    expires_at: datetime


def issue_session(
    db: Session,
    user: AppUser,
    *,
    ttl_hours: int,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
) -> IssuedSession:
    """生成新的 session 并落库, 返回明文 token + 过期时间。"""

    if ttl_hours <= 0:
        raise ValueError("ttl_hours must be positive")

    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)

    record = AppUserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=expires_at,
        user_agent=(user_agent or "")[:255] or None,
        ip=(ip or "")[:64] or None,
    )
    db.add(record)
    db.flush()
    return IssuedSession(user=user, cookie_value=token, expires_at=expires_at)


def resolve_session(db: Session, cookie_value: str) -> Optional[AppUser]:
    """根据 cookie 中的 token 找到当前活跃用户, 找不到返回 ``None``。"""
    if not cookie_value:
        return None
    token_hash = hash_token(cookie_value)
    if not token_hash:
        return None
    now = datetime.utcnow()
    session_row = (
        db.query(AppUserSession)
        .filter(
            AppUserSession.token_hash == token_hash,
            AppUserSession.revoked_at.is_(None),
            AppUserSession.expires_at > now,
        )
        .first()
    )
    if session_row is None:
        return None
    user = db.query(AppUser).filter(AppUser.id == session_row.user_id).first()
    if user is None or user.status != "active":
        return None
    return user


def revoke_session(db: Session, cookie_value: str) -> bool:
    """登出: 将匹配的 session 标记为已吊销。"""
    if not cookie_value:
        return False
    token_hash = hash_token(cookie_value)
    session_row = (
        db.query(AppUserSession)
        .filter(AppUserSession.token_hash == token_hash, AppUserSession.revoked_at.is_(None))
        .first()
    )
    if session_row is None:
        return False
    session_row.revoked_at = datetime.utcnow()
    db.add(session_row)
    return True


def revoke_all_user_sessions(db: Session, user_id: int) -> int:
    """密码重置 / 修改后吊销该用户所有 session, 返回被吊销条数。"""
    now = datetime.utcnow()
    rows = (
        db.query(AppUserSession)
        .filter(AppUserSession.user_id == user_id, AppUserSession.revoked_at.is_(None))
        .all()
    )
    for row in rows:
        row.revoked_at = now
        db.add(row)
    return len(rows)
