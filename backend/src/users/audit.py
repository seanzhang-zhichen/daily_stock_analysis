# -*- coding: utf-8 -*-
"""审计日志写入服务 (Phase 6)。

调用方采用 fire-and-forget 方式写入：写入失败只记 warning，不抛异常，
不影响主业务链路。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from src.storage import AppAuditLog

logger = logging.getLogger(__name__)


def write_audit_log(
    db: Session,
    action: str,
    *,
    user_id: Optional[int] = None,
    admin_id: Optional[int] = None,
    target_user_id: Optional[int] = None,
    target_ref: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """写入一条审计日志。失败时记 warning 并静默，不向调用方抛异常。

    设计说明：
    - 采用 fire-and-forget 模式，写入失败不影响主业务链路，
      仅记录 warning 日志供后续排查。
    - 字段长度截断：action 最长 64 字符，target_ref 最长 128 字符，
      ip 最长 64 字符，user_agent 最长 512 字符，防止超长数据导致数据库异常。
    - detail 会序列化为 JSON 字符串存储，调用方需注意敏感信息脱敏。

    Args:
        db: SQLAlchemy session（调用方保持打开状态）。
        action: 动作标识，如 ``auth.login``、``order.create``。
        user_id: 操作发起人 C 端用户 ID。
        admin_id: 操作发起人管理员用户 ID（admin 操作时填写）。
        target_user_id: 被操作目标用户 ID（admin 操作他人时填写）。
        target_ref: 业务关联标识，如订单号、退款单号、provider 名称等。
        detail: 任意附加信息（会序列化为 JSON，注意脱敏）。
        ip: 请求来源 IP。
        user_agent: 请求 User-Agent。
    """
    try:
        # 构造审计日志记录，对可变长字段做截断处理，避免超长导致数据库写入失败
        row = AppAuditLog(
            action=action[:64],
            user_id=user_id,
            admin_id=admin_id,
            target_user_id=target_user_id,
            target_ref=(target_ref or "")[:128] or None,
            detail=json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
            ip=(ip or "")[:64] or None,
            user_agent=(user_agent or "")[:512] or None,
        )
        db.add(row)
        db.commit()
    except Exception:  # noqa: BLE001
        # 审计日志写入失败不应影响主业务，记录 warning 并静默处理
        logger.warning(
            "audit log write failed action=%s user_id=%s",
            action,
            user_id,
            exc_info=True,
        )
        # 尝试回滚当前事务，避免脏数据影响后续操作
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def serialize_audit_log(row: AppAuditLog) -> dict:
    """将 AppAuditLog ORM 行序列化为前端友好的字典。

    字段映射说明：
    - 数据库字段使用下划线命名（如 user_id），返回字典使用驼峰命名（如 userId），
      便于前端 JavaScript/TypeScript 消费。
    - created_at 字段格式化为 ISO 8601 字符串（如 2024-01-01T12:00:00），
      若为空则返回 None。
    """
    return {
        "id": int(row.id),
        "action": row.action,
        "userId": row.user_id,
        "adminId": row.admin_id,
        "targetUserId": row.target_user_id,
        "targetRef": row.target_ref,
        "detail": row.detail,
        "ip": row.ip,
        "userAgent": row.user_agent,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }
