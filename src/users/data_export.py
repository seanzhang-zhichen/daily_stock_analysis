# -*- coding: utf-8 -*-
"""个人数据导出服务 (Phase 6 PIPL 合规)。

用户可在 ``/account`` 页面申请导出个人数据。本模块：

1. 从各相关表查询当前用户的数据。
2. 序列化为 JSON（脱敏：密码哈希不包含）。
3. 通过邮件发送下载内容（MVP：直接附 JSON 邮件正文；二期可改为签名 URL 链接）。

调用入口: ``request_data_export(db, user)``
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.storage import (
    AppOrder,
    AppSubscription,
    AppUserByokCredential,
    AppUserConsent,
    AppUserNotificationPref,
    AppUserWatchlist,
)
from src.users.audit import write_audit_log
from src.users.email import EmailBackend, EmailMessageDTO, get_email_backend
from src.users.errors import UserError, UserErrorCode

logger = logging.getLogger(__name__)

_BYOK_MASK = "**masked**"


def _dt(val: Optional[datetime]) -> Optional[str]:
    return val.isoformat() if val else None


def _collect_user_data(db: Session, user_id: int) -> Dict[str, Any]:
    """从各表收集用户数据并序列化（敏感字段脱敏）。"""
    from src.storage import AppUser  # noqa: PLC0415

    user: Optional[AppUser] = db.query(AppUser).filter(AppUser.id == user_id).first()
    if user is None:
        return {}

    # 自选股
    watchlists: List[AppUserWatchlist] = (
        db.query(AppUserWatchlist).filter(AppUserWatchlist.user_id == user_id).all()
    )
    # 通知偏好
    notif_pref: Optional[AppUserNotificationPref] = (
        db.query(AppUserNotificationPref)
        .filter(AppUserNotificationPref.user_id == user_id)
        .first()
    )
    # 订阅历史
    subscriptions: List[AppSubscription] = (
        db.query(AppSubscription).filter(AppSubscription.user_id == user_id).all()
    )
    # 订单
    orders: List[AppOrder] = (
        db.query(AppOrder).filter(AppOrder.user_id == user_id).all()
    )
    # BYOK 凭证（仅暴露 provider + model，不暴露密钥）
    byok: List[AppUserByokCredential] = (
        db.query(AppUserByokCredential)
        .filter(AppUserByokCredential.user_id == user_id)
        .all()
    )
    # 协议同意记录
    consents: List[AppUserConsent] = (
        db.query(AppUserConsent).filter(AppUserConsent.user_id == user_id).all()
    )

    return {
        "export_time": datetime.utcnow().isoformat(),
        "user": {
            "id": user.id,
            "email": user.email,
            "status": user.status,
            "plan_code": user.plan_code,
            "plan_expires_at": _dt(user.plan_expires_at),
            "email_verified_at": _dt(user.email_verified_at),
            "last_login_at": _dt(user.last_login_at),
            "created_at": _dt(user.created_at),
            "terms_version": user.terms_version,
        },
        "watchlists": [
            {"stock_code": w.stock_code, "stock_name": w.stock_name, "added_at": _dt(w.created_at)}
            for w in watchlists
        ],
        "notification_prefs": (
            {
                "daily_push_enabled": notif_pref.daily_push_enabled,
                "email_enabled": notif_pref.email_enabled,
                "webhook_type": notif_pref.webhook_type,
            }
            if notif_pref
            else None
        ),
        "subscriptions": [
            {
                "plan_code": s.plan_code,
                "source": s.source,
                "started_at": _dt(s.started_at),
                "expires_at": _dt(s.expires_at),
            }
            for s in subscriptions
        ],
        "orders": [
            {
                "order_no": o.order_no,
                "plan_code": o.plan_code,
                "amount_cents": o.amount_cents,
                "currency": o.currency,
                "status": o.status,
                "provider": o.provider,
                "paid_at": _dt(o.paid_at),
                "created_at": _dt(o.created_at),
            }
            for o in orders
        ],
        "byok_providers": [
            {"provider": b.provider, "model": b.model, "base_url": b.base_url, "api_key": _BYOK_MASK}
            for b in byok
        ],
        "consents": [
            {
                "terms_version": c.terms_version,
                "purpose": c.purpose,
                "agreed_at": _dt(c.agreed_at),
            }
            for c in consents
        ],
    }


def request_data_export(
    db: Session,
    *,
    user,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    email_backend: Optional[EmailBackend] = None,
) -> None:
    """收集并通过邮件发送用户个人数据导出包。

    MVP：直接把 JSON 正文发送至注册邮箱。
    二期可改为生成签名临时下载 URL（OSS / S3）。

    Raises:
        :class:`src.users.errors.UserError` — 账号已注销时拒绝导出。
    """
    if user.status == "deleted":
        raise UserError(UserErrorCode.VALIDATION_ERROR, "账号已注销，无法导出数据")

    write_audit_log(
        db,
        action="account.data_export_requested",
        user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )

    data = _collect_user_data(db, user.id)
    json_body = json.dumps(data, ensure_ascii=False, indent=2)

    backend = email_backend or get_email_backend()
    backend.send(
        EmailMessageDTO(
            to=user.email,
            subject="你的个人数据导出 - DSA 智能分析",
            body_text=(
                "你好，\n\n"
                "以下是你在 DSA 智能分析平台的个人数据导出（JSON 格式）。\n"
                "敏感字段（API Key 等）已脱敏处理。\n\n"
                "--- 数据开始 ---\n"
                f"{json_body}\n"
                "--- 数据结束 ---\n\n"
                "如有疑问，请联系客服。\n"
            ),
        )
    )
    logger.info("data export email sent to user %s", user.id)


__all__ = [
    "request_data_export",
]
