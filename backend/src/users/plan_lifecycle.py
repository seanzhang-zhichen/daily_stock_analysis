# -*- coding: utf-8 -*-
"""Plan 到期 / 续费闭环服务 (Phase 2 + Phase 4 收尾)。

职责拆解:

- ``find_users_needing_reminder(db, now)``      找到到期前 7/3/1 天的用户。
- ``find_expired_active_users(db, now)``        找到已过期但 ``plan_code != free`` 的用户。
- ``send_renewal_reminder(db, user, ...)``      发邮件、写 :class:`AppPlanReminder`、写审计。
- ``downgrade_expired_user(db, user, ...)``     回退到 free 档、写订阅记录、发邮件、写审计。
- ``run_plan_lifecycle_check(...)``             调度入口, 串起以上动作, 单用户失败不影响其它用户。

幂等控制::

    AppPlanReminder 唯一约束 (user_id, plan_code, expires_at, reminder_type) 保证
    同一档期同一类型的提醒只发一次, 重复触发时直接跳过。

调度集成::

    main.py 中 scheduled_task() 在每日推送之后调用 run_plan_lifecycle_check();
    单进程串行执行, 体量较大时再考虑并发。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.storage import (
    AppPlan,
    AppPlanReminder,
    AppSubscription,
    AppUser,
    get_db as _get_storage_db,
)
from src.users.audit import write_audit_log
from src.users.email import EmailBackend, EmailMessageDTO, get_email_backend

logger = logging.getLogger(__name__)


# 到期前提醒的天数偏移; 顺序无所谓, 但越靠近到期日的提醒优先级越高,
# 同一天匹配多个时只发提醒类型最靠后的那条 (例如剩 1 天匹配到 1d 而非 7d)。
REMINDER_OFFSET_DAYS: Sequence[int] = (7, 3, 1)
REMINDER_TYPE_EXPIRED = "expired"
_FREE_PLAN_CODE = "free"


def _reminder_type_for_days(days_left: int) -> Optional[str]:
    """根据剩余天数返回提醒类型, 不在 ``REMINDER_OFFSET_DAYS`` 内时返回 None。"""
    if days_left in REMINDER_OFFSET_DAYS:
        return f"expiring_{days_left}d"
    return None


@dataclass(frozen=True)
class ReminderCandidate:
    """一个待发送的到期提醒候选。"""

    user: AppUser
    plan_code: str
    expires_at: datetime
    days_left: int
    reminder_type: str


def find_users_needing_reminder(
    db: Session,
    *,
    now: Optional[datetime] = None,
    offsets: Sequence[int] = REMINDER_OFFSET_DAYS,
) -> List[ReminderCandidate]:
    """扫描所有 ``plan_code != free`` 且 ``plan_expires_at`` 落在 (now, now+max(offsets)] 的活跃用户。

    每条候选会按剩余天数匹配最贴近的 ``offsets`` 桶 (例如 6 天剩余匹配 7d,
    2 天剩余匹配 3d, 1 天剩余匹配 1d), 不在桶内的天数 (例如剩余 5 天) 跳过。
    """
    if not offsets:
        return []
    now = now or datetime.utcnow()
    upper_bound = now + timedelta(days=max(offsets))
    rows: List[AppUser] = (
        db.query(AppUser)
        .filter(
            AppUser.status == "active",
            AppUser.plan_code != _FREE_PLAN_CODE,
            AppUser.plan_expires_at.isnot(None),
            AppUser.plan_expires_at > now,
            AppUser.plan_expires_at <= upper_bound,
        )
        .all()
    )

    sorted_offsets = sorted(set(int(x) for x in offsets if int(x) > 0))
    candidates: List[ReminderCandidate] = []
    for user in rows:
        if user.plan_expires_at is None:
            continue
        delta = user.plan_expires_at - now
        # 向上取整: 剩余 0.3 天仍按 1 天提醒, 避免临界点丢失
        days_left = max(0, int(delta.total_seconds() // 86400))
        if delta.total_seconds() % 86400 > 0:
            days_left += 1
        # 选择 sorted_offsets 中第一个 >= days_left 的桶, 例如 days_left=2 -> 3d
        target_offset: Optional[int] = None
        for offset in sorted_offsets:
            if days_left <= offset:
                target_offset = offset
                break
        if target_offset is None:
            continue
        reminder_type = _reminder_type_for_days(target_offset)
        if reminder_type is None:
            continue
        candidates.append(
            ReminderCandidate(
                user=user,
                plan_code=user.plan_code,
                expires_at=user.plan_expires_at,
                days_left=days_left,
                reminder_type=reminder_type,
            )
        )
    return candidates


def find_expired_active_users(
    db: Session,
    *,
    now: Optional[datetime] = None,
) -> List[AppUser]:
    """找出已过期但仍挂着付费档的用户 (``plan_code != free`` 且 ``plan_expires_at < now``)。"""
    now = now or datetime.utcnow()
    return (
        db.query(AppUser)
        .filter(
            AppUser.status == "active",
            AppUser.plan_code != _FREE_PLAN_CODE,
            AppUser.plan_expires_at.isnot(None),
            AppUser.plan_expires_at < now,
        )
        .all()
    )


def _has_already_sent(
    db: Session,
    *,
    user_id: int,
    plan_code: str,
    expires_at: datetime,
    reminder_type: str,
) -> bool:
    return (
        db.query(AppPlanReminder.id)
        .filter(
            AppPlanReminder.user_id == user_id,
            AppPlanReminder.plan_code == plan_code,
            AppPlanReminder.expires_at == expires_at,
            AppPlanReminder.reminder_type == reminder_type,
        )
        .first()
        is not None
    )


def _record_reminder(
    db: Session,
    *,
    user_id: int,
    plan_code: str,
    expires_at: datetime,
    reminder_type: str,
) -> bool:
    """写入一条 :class:`AppPlanReminder`; 唯一约束冲突时安全跳过。

    返回 ``True`` 表示本次写入成功 (提醒首发); ``False`` 表示已存在跳过。
    """
    row = AppPlanReminder(
        user_id=user_id,
        plan_code=plan_code,
        expires_at=expires_at,
        reminder_type=reminder_type,
    )
    db.add(row)
    try:
        db.flush()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _format_expiry_local(expires_at: datetime) -> str:
    """把 UTC ``plan_expires_at`` 渲染为东八区可读字符串。"""
    # 内部 ``plan_expires_at`` 是 naive UTC; 渲染时转为 +08:00 以便用户阅读。
    from datetime import timezone

    aware = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
    return aware.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M (UTC+8)")


def _plan_display_name(db: Session, plan_code: str) -> str:
    plan = (
        db.query(AppPlan)
        .filter(AppPlan.code == plan_code, AppPlan.is_active.is_(True))
        .first()
    )
    if plan and plan.name:
        return str(plan.name)
    return plan_code


def _build_reminder_email(
    *, plan_name: str, days_left: int, expires_at: datetime
) -> EmailMessageDTO:
    expiry_str = _format_expiry_local(expires_at)
    subject = f"[DSA] 您的 {plan_name} 套餐还有 {days_left} 天到期"
    body = (
        f"您好，\n\n"
        f"您的 DSA「{plan_name}」套餐将在 {days_left} 天后到期 ({expiry_str})。\n"
        f"到期后将自动降级为 Free 档，并按 Free 档的每日额度限制使用。\n\n"
        f"若希望继续享有 Pro 权益，可前往会员中心一键续费：\n"
        f"  https://yourdomain.com/billing?renew=1\n\n"
        f"如已续费请忽略本邮件。\n\n"
        f"—— DSA AI 分析团队\n"
        f"本邮件由系统自动发送，请勿直接回复。"
    )
    return EmailMessageDTO(to="", subject=subject, body_text=body)


def _build_downgrade_email(*, plan_name: str, expires_at: datetime) -> EmailMessageDTO:
    expiry_str = _format_expiry_local(expires_at)
    subject = f"[DSA] 您的 {plan_name} 套餐已到期并降级为 Free"
    body = (
        f"您好，\n\n"
        f"您的 DSA「{plan_name}」套餐已于 {expiry_str} 到期，账户已自动降级为 Free 档。\n"
        f"您仍可继续使用基础功能，但每日分析次数与自选股上限会受 Free 档限制。\n\n"
        f"如希望继续享有 Pro 权益，可随时前往会员中心续费：\n"
        f"  https://yourdomain.com/billing?renew=1\n\n"
        f"—— DSA AI 分析团队\n"
        f"本邮件由系统自动发送，请勿直接回复。"
    )
    return EmailMessageDTO(to="", subject=subject, body_text=body)


def send_renewal_reminder(
    db: Session,
    candidate: ReminderCandidate,
    *,
    email_backend: Optional[EmailBackend] = None,
    dry_run: bool = False,
) -> bool:
    """发送续费提醒邮件并写入幂等记录。

    返回 ``True`` 表示本次实际触发了发送; ``False`` 表示重复跳过 / 用户无邮箱 / dry_run。
    """
    user = candidate.user
    if not user.email:
        return False
    if _has_already_sent(
        db,
        user_id=user.id,
        plan_code=candidate.plan_code,
        expires_at=candidate.expires_at,
        reminder_type=candidate.reminder_type,
    ):
        return False

    plan_name = _plan_display_name(db, candidate.plan_code)
    msg = _build_reminder_email(
        plan_name=plan_name,
        days_left=candidate.days_left,
        expires_at=candidate.expires_at,
    )
    msg = EmailMessageDTO(
        to=user.email,
        subject=msg.subject,
        body_text=msg.body_text,
        body_html=msg.body_html,
    )

    if dry_run:
        logger.info(
            "[plan-lifecycle] dry-run reminder user=%s type=%s days_left=%s",
            user.id,
            candidate.reminder_type,
            candidate.days_left,
        )
        return False

    backend = email_backend or get_email_backend()
    try:
        backend.send(msg)
    except Exception:
        logger.exception(
            "[plan-lifecycle] reminder email failed user=%s type=%s",
            user.id,
            candidate.reminder_type,
        )
        return False

    inserted = _record_reminder(
        db,
        user_id=user.id,
        plan_code=candidate.plan_code,
        expires_at=candidate.expires_at,
        reminder_type=candidate.reminder_type,
    )
    if inserted:
        write_audit_log(
            db,
            "plan.reminder_sent",
            user_id=user.id,
            target_user_id=user.id,
            target_ref=f"{candidate.plan_code}:{candidate.reminder_type}",
            detail={
                "plan_code": candidate.plan_code,
                "reminder_type": candidate.reminder_type,
                "days_left": candidate.days_left,
                "expires_at": candidate.expires_at.isoformat()
                if candidate.expires_at
                else None,
            },
        )
    return inserted


def downgrade_expired_user(
    db: Session,
    user: AppUser,
    *,
    email_backend: Optional[EmailBackend] = None,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> bool:
    """把一名已过期付费用户回退到 free 档, 写订阅记录并发出降级通知邮件。

    返回 ``True`` 表示本次执行了降级; ``False`` 表示已经是 free / dry_run 跳过 / 重复触发。
    """
    if (user.plan_code or _FREE_PLAN_CODE).lower() == _FREE_PLAN_CODE:
        return False
    if user.plan_expires_at is None:
        return False
    now = now or datetime.utcnow()
    if user.plan_expires_at >= now:
        return False

    expired_plan_code = user.plan_code
    expired_at = user.plan_expires_at

    if dry_run:
        logger.info(
            "[plan-lifecycle] dry-run downgrade user=%s plan=%s expired_at=%s",
            user.id,
            expired_plan_code,
            expired_at,
        )
        return False

    # 幂等: 同一档期 expired 类型只触发一次
    if _has_already_sent(
        db,
        user_id=user.id,
        plan_code=expired_plan_code,
        expires_at=expired_at,
        reminder_type=REMINDER_TYPE_EXPIRED,
    ):
        # 即便记录已存在, 也补一次实际状态校验, 避免重启后 plan_code 仍是 pro 的脏状态
        if (user.plan_code or _FREE_PLAN_CODE).lower() != _FREE_PLAN_CODE:
            user.plan_code = _FREE_PLAN_CODE
            user.plan_expires_at = None
            db.add(user)
            db.flush()
        return False

    plan_name = _plan_display_name(db, expired_plan_code)

    sub = AppSubscription(
        user_id=user.id,
        plan_code=_FREE_PLAN_CODE,
        source="expire",
        started_at=now,
        expires_at=None,
        note=f"auto-downgrade from {expired_plan_code} expired at {expired_at.isoformat()}"[:255],
    )
    db.add(sub)

    user.plan_code = _FREE_PLAN_CODE
    user.plan_expires_at = None
    db.add(user)
    db.flush()

    inserted = _record_reminder(
        db,
        user_id=user.id,
        plan_code=expired_plan_code,
        expires_at=expired_at,
        reminder_type=REMINDER_TYPE_EXPIRED,
    )

    write_audit_log(
        db,
        "plan.auto_downgrade",
        user_id=user.id,
        target_user_id=user.id,
        target_ref=f"{expired_plan_code}->{_FREE_PLAN_CODE}",
        detail={
            "from_plan": expired_plan_code,
            "to_plan": _FREE_PLAN_CODE,
            "expired_at": expired_at.isoformat() if expired_at else None,
            "reminder_recorded": bool(inserted),
        },
    )

    if user.email:
        msg = _build_downgrade_email(plan_name=plan_name, expires_at=expired_at)
        msg = EmailMessageDTO(
            to=user.email,
            subject=msg.subject,
            body_text=msg.body_text,
            body_html=msg.body_html,
        )
        try:
            backend = email_backend or get_email_backend()
            backend.send(msg)
        except Exception:
            logger.exception(
                "[plan-lifecycle] downgrade email failed user=%s plan=%s",
                user.id,
                expired_plan_code,
            )
    return True


@dataclass(frozen=True)
class LifecycleSummary:
    """``run_plan_lifecycle_check`` 的执行汇总, 主要供测试断言使用。"""

    reminders_sent: int
    reminders_skipped: int
    downgraded: int
    downgrade_skipped: int


def run_plan_lifecycle_check(
    *,
    db: Optional[Session] = None,
    email_backend: Optional[EmailBackend] = None,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> LifecycleSummary:
    """Plan 到期 / 续费闭环统一入口。

    ``db`` 可选; 不传时通过 :func:`src.storage.get_db` 获取一个 session_scope。
    """
    if db is not None:
        return _run_with_session(db, email_backend=email_backend, now=now, dry_run=dry_run)

    storage = _get_storage_db()
    with storage.session_scope() as session:
        return _run_with_session(session, email_backend=email_backend, now=now, dry_run=dry_run)


def _run_with_session(
    db: Session,
    *,
    email_backend: Optional[EmailBackend],
    now: Optional[datetime],
    dry_run: bool,
) -> LifecycleSummary:
    now = now or datetime.utcnow()
    backend = email_backend or get_email_backend()

    reminders_sent = 0
    reminders_skipped = 0
    downgraded = 0
    downgrade_skipped = 0

    candidates = find_users_needing_reminder(db, now=now)
    for candidate in candidates:
        try:
            ok = send_renewal_reminder(
                db, candidate, email_backend=backend, dry_run=dry_run
            )
            if ok:
                reminders_sent += 1
            else:
                reminders_skipped += 1
        except Exception:
            logger.exception(
                "[plan-lifecycle] reminder failed user=%s type=%s",
                candidate.user.id,
                candidate.reminder_type,
            )
            reminders_skipped += 1

    expired_users = find_expired_active_users(db, now=now)
    for user in expired_users:
        try:
            ok = downgrade_expired_user(
                db, user, email_backend=backend, now=now, dry_run=dry_run
            )
            if ok:
                downgraded += 1
            else:
                downgrade_skipped += 1
        except Exception:
            logger.exception(
                "[plan-lifecycle] downgrade failed user=%s plan=%s",
                user.id,
                user.plan_code,
            )
            downgrade_skipped += 1

    summary = LifecycleSummary(
        reminders_sent=reminders_sent,
        reminders_skipped=reminders_skipped,
        downgraded=downgraded,
        downgrade_skipped=downgrade_skipped,
    )
    logger.info(
        "[plan-lifecycle] completed sent=%s skipped=%s downgraded=%s downgrade_skipped=%s dry_run=%s",
        summary.reminders_sent,
        summary.reminders_skipped,
        summary.downgraded,
        summary.downgrade_skipped,
        dry_run,
    )
    return summary


__all__ = [
    "REMINDER_OFFSET_DAYS",
    "REMINDER_TYPE_EXPIRED",
    "ReminderCandidate",
    "LifecycleSummary",
    "downgrade_expired_user",
    "find_expired_active_users",
    "find_users_needing_reminder",
    "run_plan_lifecycle_check",
    "send_renewal_reminder",
]
