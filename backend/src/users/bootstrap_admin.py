# -*- coding: utf-8 -*-
"""从部署环境变量引导平台超级管理员账号。

在应用启动时调用，确保至少存在一个管理员账号：若环境变量未配置则跳过；
若账号已存在则直接提升为管理员（默认不改密码，除非显式开启密码同步）。
仅在缺失账号时校验密码强度，避免无谓的失败。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.storage.models.app import AppUser
from src.users.consents import CURRENT_TERMS_VERSION
from src.users.credits import ensure_referral_code
from src.users.passwords import (
    hash_password,
    is_valid_email,
    validate_password_strength,
)

logger = logging.getLogger(__name__)

SUPER_ADMIN_EMAIL_ENV = "SUPER_ADMIN_EMAIL"
SUPER_ADMIN_PASSWORD_ENV = "SUPER_ADMIN_PASSWORD"
SUPER_ADMIN_SYNC_PASSWORD_ENV = "SUPER_ADMIN_SYNC_PASSWORD"
_FALSEY_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class SuperAdminBootstrapResult:
    """单次超级管理员引导操作的执行摘要。

    由 ``bootstrap_super_admin_from_env`` 返回，便于上层调用方区分
    「未启用」「邮箱无效」「密码无效」「账户不存在」等不同的跳过原因，
    并区分创建/提升/同步密码等不同的副作用。
    """

    enabled: bool
    email: Optional[str] = None
    created: bool = False
    granted_admin: bool = False
    password_updated: bool = False
    skipped_reason: Optional[str] = None


def _normalize_email(raw_email: Optional[str]) -> str:
    """规范化邮箱字符串，做去空白与大小写归一。"""
    return (raw_email or "").strip().lower()


def _current_terms_version() -> str:
    """读取当前生效的服务条款版本。

    优先使用环境变量 ``USER_TERMS_VERSION``，未配置时回退到 ``CURRENT_TERMS_VERSION``。
    去除首尾空白后若仍为空，也兜底使用默认值，避免写入空字符串。
    """
    return (os.getenv("USER_TERMS_VERSION") or CURRENT_TERMS_VERSION).strip() or CURRENT_TERMS_VERSION


def _parse_env_bool(value: Optional[str], default: bool = False) -> bool:
    """解析布尔型环境变量。

    采用宽松策略：未设置/全空白使用 ``default``；非空白字符串若不属于
    ``_FALSEY_VALUES`` 集合则视为 True，从而兼容 ``true/1/yes/on`` 等常见写法。
    """
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized not in _FALSEY_VALUES


def bootstrap_super_admin_from_env(db: Session) -> SuperAdminBootstrapResult:
    """按环境变量配置创建或提升平台超级管理员。

    若未配置 ``SUPER_ADMIN_EMAIL`` 则整体跳过（返回 ``enabled=False``）。
    创建缺失账户时需要同时提供 ``SUPER_ADMIN_PASSWORD`` 且通过强度校验；
    已存在的账户默认仅授予管理员权限而不会覆盖密码。
    若希望每次启动都强制把已存在账户的密码哈希同步为环境变量值，
    可将 ``SUPER_ADMIN_SYNC_PASSWORD=true``。

    Args:
        db: 数据库会话，用于查询/创建/更新 ``AppUser`` 记录。

    Returns:
        :class:`SuperAdminBootstrapResult`，描述本次引导操作是否执行、
        是否新建账户、是否提升为管理员、是否同步密码，以及跳过原因。
    """

    email = _normalize_email(os.getenv(SUPER_ADMIN_EMAIL_ENV))
    # 未配置邮箱则视为功能关闭，整个流程直接跳过，避免误创建。
    if not email:
        return SuperAdminBootstrapResult(enabled=False, skipped_reason="not_configured")

    if not is_valid_email(email):
        logger.warning("%s is not a valid email; skipping super-admin bootstrap", SUPER_ADMIN_EMAIL_ENV)
        return SuperAdminBootstrapResult(enabled=True, email=email, skipped_reason="invalid_email")

    raw_password = os.getenv(SUPER_ADMIN_PASSWORD_ENV) or ""
    password = raw_password.strip()
    sync_password = _parse_env_bool(os.getenv(SUPER_ADMIN_SYNC_PASSWORD_ENV), default=False)
    terms_version = _current_terms_version()

    user = db.query(AppUser).filter(AppUser.email == email).first()
    # 记录提权前的管理员状态，便于在结果中区分「本就已是管理员」与「本次新授予」。
    was_admin = bool(getattr(user, "is_admin", False)) if user is not None else False
    created = False
    password_updated = False

    if user is None:
        # 仅在新建账户时校验密码强度，避免对已存在账户进行无谓的失败校验。
        password_error = validate_password_strength(password)
        if password_error:
            logger.warning(
                "%s is required and must be valid when creating %s=%s: %s",
                SUPER_ADMIN_PASSWORD_ENV,
                SUPER_ADMIN_EMAIL_ENV,
                email,
                password_error,
            )
            return SuperAdminBootstrapResult(
                enabled=True,
                email=email,
                skipped_reason="invalid_password",
            )
        user = AppUser(
            email=email,
            password_hash=hash_password(password),
            status="active",
            plan_code="free",
            email_verified_at=datetime.utcnow(),
            is_admin=True,
            terms_version=terms_version,
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            # 兜底处理并发场景：邮箱唯一索引冲突时回滚，重新查询以沿用竞态创建的账户。
            db.rollback()
            user = db.query(AppUser).filter(AppUser.email == email).first()
            if user is None:
                raise
        else:
            created = True
            password_updated = True

    if user is None:
        return SuperAdminBootstrapResult(enabled=True, email=email, skipped_reason="not_found")

    if sync_password and not created:
        # 显式开启密码同步时才覆盖已有账户的密码哈希，默认不动既有密码。
        password_error = validate_password_strength(password)
        if password_error:
            logger.warning(
                "%s=true but %s is invalid for %s: %s",
                SUPER_ADMIN_SYNC_PASSWORD_ENV,
                SUPER_ADMIN_PASSWORD_ENV,
                email,
                password_error,
            )
            return SuperAdminBootstrapResult(
                enabled=True,
                email=email,
                created=False,
                granted_admin=False,
                skipped_reason="invalid_password",
            )
        user.password_hash = hash_password(password)
        password_updated = True

    # 以下字段无论新建还是已存在账户都无条件刷新，保证管理员状态最终一致。
    user.is_admin = True
    user.status = "active"
    # 清理待注销标记，避免管理员被误判为正在注销流程中。
    user.deletion_requested_at = None
    if user.email_verified_at is None:
        user.email_verified_at = datetime.utcnow()
    user.terms_version = terms_version
    ensure_referral_code(db, user)
    db.add(user)
    db.commit()

    result = SuperAdminBootstrapResult(
        enabled=True,
        email=email,
        created=created,
        granted_admin=not was_admin,
        password_updated=password_updated,
    )
    logger.info(
        "Super admin bootstrap applied: email=%s created=%s granted_admin=%s password_updated=%s",
        result.email,
        result.created,
        result.granted_admin,
        result.password_updated,
    )
    return result


__all__ = [
    "SUPER_ADMIN_EMAIL_ENV",
    "SUPER_ADMIN_PASSWORD_ENV",
    "SUPER_ADMIN_SYNC_PASSWORD_ENV",
    "SuperAdminBootstrapResult",
    "bootstrap_super_admin_from_env",
]
