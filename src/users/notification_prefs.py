# -*- coding: utf-8 -*-
"""用户通知偏好服务 (Phase 3)。

封装对 ``app_user_notification_prefs`` 表的读写。
每个用户至多一行；首次读取时若不存在则返回默认值，写入时 upsert。

用法示例::

    from src.users.notification_prefs import get_prefs, update_prefs

    prefs = get_prefs(db, user_id=1)
    update_prefs(db, user_id=1, daily_push_enabled=True)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from src.storage import AppUserNotificationPref
from src.users.errors import UserError, UserErrorCode

ALLOWED_WEBHOOK_TYPES = {"feishu", "wecom", "dingtalk", "discord", "telegram", "generic", "custom"}


@dataclass
class NotificationPrefs:
    """用户通知偏好快照（可变）。"""

    daily_push_enabled: bool = False
    email_enabled: bool = True
    webhook_url: Optional[str] = None
    webhook_type: Optional[str] = None


def get_prefs(db: Session, *, user_id: int) -> NotificationPrefs:
    """读取用户通知偏好；若行不存在则返回系统默认值。"""
    row = (
        db.query(AppUserNotificationPref)
        .filter(AppUserNotificationPref.user_id == user_id)
        .first()
    )
    if row is None:
        return NotificationPrefs()
    return NotificationPrefs(
        daily_push_enabled=bool(row.daily_push_enabled),
        email_enabled=bool(row.email_enabled),
        webhook_url=row.webhook_url or None,
        webhook_type=row.webhook_type or None,
    )


def update_prefs(
    db: Session,
    *,
    user_id: int,
    daily_push_enabled: Optional[bool] = None,
    email_enabled: Optional[bool] = None,
    webhook_url: Optional[str] = None,
    webhook_type: Optional[str] = None,
    clear_webhook: bool = False,
    can_webhook: bool = False,
    can_email_notifications: bool = False,
) -> NotificationPrefs:
    """Upsert 用户通知偏好。

    - ``webhook_url`` / ``webhook_type`` 仅当 ``can_webhook=True`` 时才被写入；
      否则将被忽略（避免免费档绕过套餐限制）。
    - ``clear_webhook=True`` 可删除 Webhook 配置，无需 Pro 权限。
    """
    if webhook_url is not None and not can_webhook:
        raise UserError(UserErrorCode.PERMISSION_DENIED, "Webhook 通知需要 Pro 套餐")
    if email_enabled is True and not can_email_notifications:
        raise UserError(UserErrorCode.PERMISSION_DENIED, "邮件通知需要 Pro 套餐")
    if daily_push_enabled is True and not can_email_notifications:
        raise UserError(UserErrorCode.PERMISSION_DENIED, "每日推送需要 Pro 套餐")

    if webhook_type is not None and webhook_type not in ALLOWED_WEBHOOK_TYPES:
        raise UserError(
            UserErrorCode.VALIDATION_ERROR,
            f"不支持的 webhook_type: {webhook_type}，允许值: {', '.join(sorted(ALLOWED_WEBHOOK_TYPES))}",
        )

    row = (
        db.query(AppUserNotificationPref)
        .filter(AppUserNotificationPref.user_id == user_id)
        .first()
    )
    if row is None:
        row = AppUserNotificationPref(user_id=user_id)
        db.add(row)

    if daily_push_enabled is not None:
        row.daily_push_enabled = daily_push_enabled
    if email_enabled is not None:
        row.email_enabled = email_enabled
    if clear_webhook:
        row.webhook_url = None
        row.webhook_type = None
    elif can_webhook:
        if webhook_url is not None:
            row.webhook_url = (webhook_url or "").strip() or None
        if webhook_type is not None:
            row.webhook_type = webhook_type

    db.flush()
    return NotificationPrefs(
        daily_push_enabled=bool(row.daily_push_enabled),
        email_enabled=bool(row.email_enabled),
        webhook_url=row.webhook_url or None,
        webhook_type=row.webhook_type or None,
    )


def get_users_with_daily_push(db: Session) -> list[int]:
    """返回所有开启了每日推送的用户 ID 列表（供调度器使用）。"""
    rows = (
        db.query(AppUserNotificationPref.user_id)
        .filter(AppUserNotificationPref.daily_push_enabled.is_(True))
        .all()
    )
    return [r.user_id for r in rows]
