# -*- coding: utf-8 -*-
"""Bootstrap a platform super admin from deployment environment variables."""

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
    """Summary of one super-admin bootstrap attempt."""

    enabled: bool
    email: Optional[str] = None
    created: bool = False
    granted_admin: bool = False
    password_updated: bool = False
    skipped_reason: Optional[str] = None


def _normalize_email(raw_email: Optional[str]) -> str:
    return (raw_email or "").strip().lower()


def _current_terms_version() -> str:
    return (os.getenv("USER_TERMS_VERSION") or CURRENT_TERMS_VERSION).strip() or CURRENT_TERMS_VERSION


def _parse_env_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized not in _FALSEY_VALUES


def bootstrap_super_admin_from_env(db: Session) -> SuperAdminBootstrapResult:
    """Create or promote the configured platform super admin.

    Required for creating a missing account:
    - ``SUPER_ADMIN_EMAIL``
    - ``SUPER_ADMIN_PASSWORD``

    Existing accounts are promoted without changing their password by default.
    Set ``SUPER_ADMIN_SYNC_PASSWORD=true`` to force the stored password hash to
    follow ``SUPER_ADMIN_PASSWORD`` on every startup.
    """

    email = _normalize_email(os.getenv(SUPER_ADMIN_EMAIL_ENV))
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
    was_admin = bool(getattr(user, "is_admin", False)) if user is not None else False
    created = False
    password_updated = False

    if user is None:
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

    user.is_admin = True
    user.status = "active"
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
