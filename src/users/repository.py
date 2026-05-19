# -*- coding: utf-8 -*-
"""``app_users`` / ``app_user_*`` 系列表的查询封装。

仓储层只做 CRUD, 不写业务规则。所有调用方都需要自行管理 :class:`Session`
的事务生命周期 (Service 层负责)。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from src.storage import (
    AppUser,
    AppUserEmailVerification,
)
from src.users.passwords import hash_token


def get_user_by_email(db: Session, email: str) -> Optional[AppUser]:
    if not email:
        return None
    normalized = email.strip().lower()
    return db.query(AppUser).filter(AppUser.email == normalized).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[AppUser]:
    if not user_id:
        return None
    return db.query(AppUser).filter(AppUser.id == int(user_id)).first()


def create_user(
    db: Session,
    *,
    email: str,
    password_hash: str,
    plan_code: str = "free",
    email_verified: bool = False,
) -> AppUser:
    record = AppUser(
        email=email.strip().lower(),
        password_hash=password_hash,
        status="active",
        plan_code=plan_code,
        email_verified_at=datetime.utcnow() if email_verified else None,
    )
    db.add(record)
    db.flush()
    return record


def update_password(db: Session, user: AppUser, new_password_hash: str) -> AppUser:
    user.password_hash = new_password_hash
    db.add(user)
    db.flush()
    return user


def mark_email_verified(db: Session, user: AppUser) -> AppUser:
    if user.email_verified_at is None:
        user.email_verified_at = datetime.utcnow()
        db.add(user)
        db.flush()
    return user


def touch_last_login(db: Session, user: AppUser) -> AppUser:
    user.last_login_at = datetime.utcnow()
    db.add(user)
    db.flush()
    return user


def create_verification_token(
    db: Session,
    *,
    user_id: int,
    raw_token: str,
    purpose: str,
    ttl_hours: int,
) -> AppUserEmailVerification:
    expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)
    record = AppUserEmailVerification(
        user_id=user_id,
        token_hash=hash_token(raw_token),
        purpose=purpose,
        expires_at=expires_at,
    )
    db.add(record)
    db.flush()
    return record


def consume_verification_token(
    db: Session,
    *,
    raw_token: str,
    purpose: str,
) -> Optional[AppUserEmailVerification]:
    """查找并占用一次性 token。占用成功返回行, 否则返回 ``None``。"""
    if not raw_token:
        return None
    token_hash = hash_token(raw_token)
    now = datetime.utcnow()
    row = (
        db.query(AppUserEmailVerification)
        .filter(
            AppUserEmailVerification.token_hash == token_hash,
            AppUserEmailVerification.purpose == purpose,
            AppUserEmailVerification.consumed_at.is_(None),
            AppUserEmailVerification.expires_at > now,
        )
        .first()
    )
    if row is None:
        return None
    row.consumed_at = now
    db.add(row)
    db.flush()
    return row
