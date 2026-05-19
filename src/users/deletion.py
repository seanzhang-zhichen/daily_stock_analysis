# -*- coding: utf-8 -*-
"""账号注销服务 (Phase 6 PIPL 合规)。

流程：用户提交注销申请 → 7 天冷静期（可取消）→ 软删（status='deleted'）
      → 30 天后物理清理个人数据；保留订单与发票（财税法规 5 年）。

提供：

- :func:`request_deletion`         — 发起注销申请（写 deletion_requested_at，立即撤销会话）
- :func:`cancel_deletion`          — 冷静期内取消注销
- :func:`execute_pending_deletions` — 调度器每日调用：将超过 7 天的申请软删
- :func:`cleanup_deleted_users`    — 调度器每日调用：将软删超过 30 天的账号物理清除个人数据
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.storage import AppUser
from src.users import repository as repo
from src.users.audit import write_audit_log
from src.users.email import EmailBackend, EmailMessageDTO, get_email_backend
from src.users.errors import UserError, UserErrorCode
from src.users.sessions import revoke_all_user_sessions

logger = logging.getLogger(__name__)

_COOLING_OFF_DAYS = 7    # 冷静期（申请后 N 天执行软删）
_RETENTION_DAYS = 30     # 软删后物理清除等待天数


# ── 申请 / 取消 ──────────────────────────────────────────────────────────────


def request_deletion(
    db: Session,
    *,
    user: AppUser,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    email_backend: Optional[EmailBackend] = None,
) -> None:
    """发起账号注销申请。

    - 若账号已在冷静期内，幂等返回（不重置计时）。
    - 立即撤销所有 session，用户将被迫下线。
    - 发送确认邮件，告知冷静期截止日期与取消方式。
    """
    if user.status == "deleted":
        raise UserError(UserErrorCode.VALIDATION_ERROR, "账号已注销")

    if user.deletion_requested_at is not None:
        logger.info("user %s already in deletion cooling-off period", user.id)
        return

    now = datetime.utcnow()
    user.deletion_requested_at = now
    db.add(user)
    db.commit()

    revoke_all_user_sessions(db, user.id)

    write_audit_log(
        db,
        action="account.deletion_requested",
        user_id=user.id,
        detail=json.dumps({"cooling_off_days": _COOLING_OFF_DAYS}),
        ip=ip,
        user_agent=user_agent,
    )

    deadline = now + timedelta(days=_COOLING_OFF_DAYS)
    backend = email_backend or get_email_backend()
    try:
        backend.send(
            EmailMessageDTO(
                to=user.email,
                subject="账号注销申请已收到 - DSA 智能分析",
                body_text=(
                    f"你好，\n\n"
                    f"我们已收到你的账号注销申请。\n\n"
                    f"冷静期截止时间：{deadline.strftime('%Y-%m-%d %H:%M')} UTC\n"
                    f"在此期间内，你可以随时登录账号中心取消注销。\n\n"
                    f"冷静期结束后，你的账号将被标记为已注销，个人数据将在 {_RETENTION_DAYS} 天后物理清除。\n"
                    f"订单与发票记录将按财税法规保留 5 年。\n\n"
                    f"如不是你本人操作，请立即联系客服。\n"
                ),
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning("deletion confirmation email failed for user %s", user.id, exc_info=True)


def cancel_deletion(
    db: Session,
    *,
    user: AppUser,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """取消冷静期内的注销申请。"""
    if user.deletion_requested_at is None:
        raise UserError(UserErrorCode.VALIDATION_ERROR, "没有待处理的注销申请")

    user.deletion_requested_at = None
    db.add(user)
    db.commit()

    write_audit_log(
        db,
        action="account.deletion_cancelled",
        user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    logger.info("user %s cancelled deletion request", user.id)


# ── 调度器：软删 + 物理清除 ──────────────────────────────────────────────────


def execute_pending_deletions(db: Session) -> int:
    """软删冷静期已过的注销申请。

    将 ``deletion_requested_at`` 早于 ``now - COOLING_OFF_DAYS`` 且
    ``status='active'`` 的用户标记为 ``status='deleted'``，并撤销所有 session。

    Returns:
        本次软删的用户数。
    """
    cutoff = datetime.utcnow() - timedelta(days=_COOLING_OFF_DAYS)
    users: List[AppUser] = (
        db.query(AppUser)
        .filter(
            AppUser.deletion_requested_at <= cutoff,
            AppUser.status == "active",
        )
        .all()
    )

    count = 0
    for user in users:
        try:
            revoke_all_user_sessions(db, user.id)
            user.status = "deleted"
            user.updated_at = datetime.utcnow()
            db.add(user)
            db.commit()
            write_audit_log(
                db,
                action="account.deleted_soft",
                user_id=user.id,
                detail=json.dumps({"reason": "cooling_off_expired"}),
            )
            count += 1
            logger.info("soft-deleted user %s (email: %s)", user.id, user.email)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.error("failed to soft-delete user %s", user.id, exc_info=True)

    return count


def cleanup_deleted_users(db: Session, *, dry_run: bool = False) -> int:
    """物理清除已软删且超过保留期的用户个人数据。

    规则：
    - ``status='deleted'`` 且 ``deletion_requested_at`` 早于 ``now - (COOLING_OFF + RETENTION)``
    - 清除：email / password_hash / deletion_requested_at（置为 NULL）、自选股、通知偏好、BYOK 凭证、session
    - 保留：id / status / plan_code / created_at / updated_at（统计用）；订单 / 发票 / 审计日志（财税合规）

    Args:
        dry_run: 若为 True 只扫描不写库。

    Returns:
        处理的用户数。
    """
    cutoff = datetime.utcnow() - timedelta(days=_COOLING_OFF_DAYS + _RETENTION_DAYS)
    users: List[AppUser] = (
        db.query(AppUser)
        .filter(
            AppUser.deletion_requested_at <= cutoff,
            AppUser.status == "deleted",
        )
        .all()
    )

    count = 0
    for user in users:
        if dry_run:
            logger.info("[dry-run] would purge personal data for user %s", user.id)
            count += 1
            continue
        try:
            _purge_user_personal_data(db, user)
            count += 1
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.error("failed to purge personal data for user %s", user.id, exc_info=True)

    return count


def _purge_user_personal_data(db: Session, user: AppUser) -> None:
    """清除单个用户的个人数据（不删行，只置空敏感字段）。"""
    uid = user.id

    # 清除关联表个人数据
    db.execute(text("DELETE FROM app_user_watchlists WHERE user_id = :uid"), {"uid": uid})
    db.execute(text("DELETE FROM app_user_notification_prefs WHERE user_id = :uid"), {"uid": uid})
    db.execute(text("DELETE FROM app_user_byok_credentials WHERE user_id = :uid"), {"uid": uid})
    db.execute(text("DELETE FROM app_user_sessions WHERE user_id = :uid"), {"uid": uid})
    db.execute(text("DELETE FROM app_user_email_verifications WHERE user_id = :uid"), {"uid": uid})

    # 脱敏用户主行（保留 id / status / created_at 用于统计）
    placeholder_email = f"deleted_{uid}@purged.invalid"
    user.email = placeholder_email
    user.password_hash = "purged"
    user.plan_code = "free"
    user.plan_expires_at = None
    user.email_verified_at = None
    user.deletion_requested_at = None
    user.terms_version = None
    user.updated_at = datetime.utcnow()
    db.add(user)

    db.commit()
    write_audit_log(
        db,
        action="account.personal_data_purged",
        user_id=uid,
        detail=json.dumps({"placeholder_email": placeholder_email}),
    )
    logger.info("purged personal data for user %s → %s", uid, placeholder_email)


__all__ = [
    "request_deletion",
    "cancel_deletion",
    "execute_pending_deletions",
    "cleanup_deleted_users",
    "COOLING_OFF_DAYS",
    "RETENTION_DAYS",
]

COOLING_OFF_DAYS = _COOLING_OFF_DAYS
RETENTION_DAYS = _RETENTION_DAYS
