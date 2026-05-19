# -*- coding: utf-8 -*-
"""平台运营后台 endpoint (Phase 5/6)。

挂载位置: ``/api/v1/admin/*``。

权限:
- 所有端点都要求当前 session 对应的 :class:`AppUser` 设置了 ``is_admin=True``。
- 普通用户访问返回 403; 未登录返回 401。
- 引导首个 admin 可通过 ``scripts/grant_admin.py`` 在数据库里直接置位。

提供的能力:
- ``GET /admin/me``: 健康检查 + 当前 admin 信息
- ``GET /admin/users``: 用户列表 (分页 / 邮箱筛选)
- ``GET /admin/orders``: 订单列表 (按状态 / 用户 / provider 过滤)
- ``GET /admin/refunds``: 退款列表
- ``POST /admin/refunds/{refund_no}/approve``: 审核通过 + 通道退款 + 立即降级
- ``POST /admin/refunds/{refund_no}/reject``: 审核拒绝
- ``GET /admin/invoices``: 发票列表
- ``POST /admin/invoices/{invoice_no}/issue``: 标记已开具 (附下载 URL)
- ``POST /admin/invoices/{invoice_no}/reject``: 拒绝发票申请
- ``POST /admin/grant-plan``: 手动开通套餐 (§11.10 兜底 / KOL / 客服补单)
- ``GET /admin/stats``: 简单聚合指标
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import get_admin_user, get_db
from src.storage import (
    AppAuditLog,
    AppInvoice,
    AppOrder,
    AppRefund,
    AppSubscription,
    AppUser,
)
from src.users.audit import serialize_audit_log, write_audit_log
from src.users.email import EmailMessageDTO, get_email_backend
from src.services.billing import OrderService
from src.services.billing.order_service import (
    serialize_invoice,
    serialize_order,
    serialize_refund,
)
from src.users.plans import grant_plan as svc_grant_plan

logger = logging.getLogger(__name__)
router = APIRouter()
_svc = OrderService()


# --- Schemas ---------------------------------------------------------------


class ApproveRefundRequest(BaseModel):
    model_config = {"populate_by_name": True}
    provider_refund_no: Optional[str] = Field(default=None, alias="providerRefundNo")


class RejectRefundRequest(BaseModel):
    note: Optional[str] = Field(default=None)


class IssueInvoiceRequest(BaseModel):
    model_config = {"populate_by_name": True}
    issued_url: Optional[str] = Field(default=None, alias="issuedUrl")


class GrantPlanRequest(BaseModel):
    model_config = {"populate_by_name": True}
    user_id: int = Field(..., alias="userId")
    plan_code: str = Field(..., alias="planCode")
    grant_days: int = Field(..., alias="grantDays")
    note: Optional[str] = Field(default=None)


# --- Helpers ---------------------------------------------------------------


def _notify_refund_result(refund: AppRefund, user: Optional[AppUser], *, approved: bool, note: Optional[str] = None) -> None:
    """退款审核后异步发邮件通知用户（失败只记日志，不影响主流程）。"""
    if user is None or not user.email:
        return
    try:
        amount_yuan = f"¥{refund.amount_cents / 100:.2f}"
        if approved:
            subject = "[DSA] 您的退款申请已通过"
            body = (
                f"您好，\n\n"
                f"您申请退款的订单（{refund.order_no}）已审核通过。\n"
                f"退款金额：{amount_yuan}\n"
                f"退款将在 3–5 个工作日内原路退回您的支付账户。\n\n"
                f"退款单号：{refund.refund_no}\n"
                f"如有疑问，请联系客服。\n\n"
                f"—— DSA AI 分析团队\n"
                f"本邮件由系统自动发送，请勿直接回复。"
            )
        else:
            subject = "[DSA] 您的退款申请未通过"
            reason_line = f"\n拒绝原因：{note}" if note else ""
            body = (
                f"您好，\n\n"
                f"很遗憾，您申请退款的订单（{refund.order_no}）未能通过审核。{reason_line}\n\n"
                f"如对此结果有异议，请联系客服说明情况，我们将尽力协助您解决。\n\n"
                f"—— DSA AI 分析团队\n"
                f"本邮件由系统自动发送，请勿直接回复。"
            )
        msg = EmailMessageDTO(to=user.email, subject=subject, body_text=body)
        get_email_backend().send(msg)
    except Exception:
        logger.exception("退款通知邮件发送失败 refund_no=%s", refund.refund_no)


def _serialize_admin_user(user: AppUser) -> dict:
    return {
        "id": int(user.id),
        "email": user.email,
        "plan": user.plan_code,
        "planExpiresAt": user.plan_expires_at.isoformat() if user.plan_expires_at else None,
        "isAdmin": bool(getattr(user, "is_admin", False)),
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "lastLoginAt": user.last_login_at.isoformat() if user.last_login_at else None,
        "termsVersion": getattr(user, "terms_version", None),
        "status": user.status,
    }


# --- /admin/me -------------------------------------------------------------


@router.get("/me", summary="(admin) 当前 admin 信息 + 心跳")
async def admin_me(current_admin: AppUser = Depends(get_admin_user)):
    return {"admin": _serialize_admin_user(current_admin)}


# --- /admin/users ----------------------------------------------------------


@router.get("/users", summary="(admin) 用户列表")
async def admin_list_users(
    db: Session = Depends(get_db),
    _: AppUser = Depends(get_admin_user),
    email_like: Optional[str] = Query(default=None, alias="emailLike"),
    plan_code: Optional[str] = Query(default=None, alias="planCode"),
    is_admin: Optional[bool] = Query(default=None, alias="isAdmin"),
    limit: int = Query(default=100, ge=1, le=500),
):
    q = db.query(AppUser)
    if email_like:
        q = q.filter(AppUser.email.ilike(f"%{email_like.strip()}%"))
    if plan_code:
        q = q.filter(AppUser.plan_code == plan_code)
    if is_admin is not None:
        q = q.filter(AppUser.is_admin.is_(bool(is_admin)))
    rows = q.order_by(AppUser.created_at.desc()).limit(limit).all()
    return {"users": [_serialize_admin_user(u) for u in rows], "count": len(rows)}


# --- /admin/orders ---------------------------------------------------------


@router.get("/orders", summary="(admin) 订单列表")
async def admin_list_orders(
    db: Session = Depends(get_db),
    _: AppUser = Depends(get_admin_user),
    status: Optional[str] = Query(default=None),
    user_id: Optional[int] = Query(default=None, alias="userId"),
    provider: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    orders = _svc.list_orders_admin(
        db,
        status=status,
        user_id=user_id,
        provider=provider,
        limit=limit,
    )
    return {"orders": [serialize_order(o) for o in orders], "count": len(orders)}


@router.get("/orders/{order_no}", summary="(admin) 订单详情")
async def admin_get_order(
    order_no: str,
    db: Session = Depends(get_db),
    _: AppUser = Depends(get_admin_user),
):
    order = _svc.get_order(db, order_no)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return {"order": serialize_order(order)}


# --- /admin/refunds --------------------------------------------------------


@router.get("/refunds", summary="(admin) 退款列表")
async def admin_list_refunds(
    db: Session = Depends(get_db),
    _: AppUser = Depends(get_admin_user),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    refunds = _svc.list_refunds_admin(db, status=status, limit=limit)
    return {"refunds": [serialize_refund(r) for r in refunds], "count": len(refunds)}


@router.post("/refunds/{refund_no}/approve", summary="(admin) 审核通过退款")
async def admin_approve_refund(
    refund_no: str,
    db: Session = Depends(get_db),
    current_admin: AppUser = Depends(get_admin_user),
    body: ApproveRefundRequest = Body(default=ApproveRefundRequest()),
):
    refund = _svc.get_refund(db, refund_no)
    if refund is None:
        raise HTTPException(status_code=404, detail="退款单不存在")
    try:
        refund = _svc.approve_refund(
            db,
            refund,
            reviewer=current_admin,
            provider_refund_no=body.provider_refund_no,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit_log(
        db, "refund.approve",
        admin_id=int(current_admin.id),
        target_ref=refund_no,
        detail={"providerRefundNo": body.provider_refund_no},
    )
    user = db.query(AppUser).filter(AppUser.id == refund.user_id).first()
    _notify_refund_result(refund, user, approved=True)
    return {"refund": serialize_refund(refund)}


@router.post("/refunds/{refund_no}/reject", summary="(admin) 审核拒绝退款")
async def admin_reject_refund(
    refund_no: str,
    db: Session = Depends(get_db),
    current_admin: AppUser = Depends(get_admin_user),
    body: RejectRefundRequest = Body(default=RejectRefundRequest()),
):
    refund = _svc.get_refund(db, refund_no)
    if refund is None:
        raise HTTPException(status_code=404, detail="退款单不存在")
    try:
        refund = _svc.reject_refund(db, refund, reviewer=current_admin, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit_log(
        db, "refund.reject",
        admin_id=int(current_admin.id),
        target_ref=refund_no,
        detail={"note": body.note},
    )
    user = db.query(AppUser).filter(AppUser.id == refund.user_id).first()
    _notify_refund_result(refund, user, approved=False, note=body.note)
    return {"refund": serialize_refund(refund)}


# --- /admin/invoices -------------------------------------------------------


@router.get("/invoices", summary="(admin) 发票申请列表")
async def admin_list_invoices(
    db: Session = Depends(get_db),
    _: AppUser = Depends(get_admin_user),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    invoices = _svc.list_invoices_admin(db, status=status, limit=limit)
    return {"invoices": [serialize_invoice(i) for i in invoices], "count": len(invoices)}


@router.post("/invoices/{invoice_no}/issue", summary="(admin) 标记发票已开具")
async def admin_issue_invoice(
    invoice_no: str,
    db: Session = Depends(get_db),
    current_admin: AppUser = Depends(get_admin_user),
    body: IssueInvoiceRequest = Body(default=IssueInvoiceRequest()),
):
    invoice = _svc.get_invoice(db, invoice_no)
    if invoice is None:
        raise HTTPException(status_code=404, detail="发票申请不存在")
    try:
        invoice = _svc.issue_invoice(
            db, invoice, reviewer=current_admin, issued_url=body.issued_url
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit_log(
        db, "invoice.issue",
        admin_id=int(current_admin.id),
        target_ref=invoice_no,
        detail={"issuedUrl": body.issued_url},
    )
    return {"invoice": serialize_invoice(invoice)}


@router.post("/invoices/{invoice_no}/reject", summary="(admin) 拒绝发票申请")
async def admin_reject_invoice(
    invoice_no: str,
    db: Session = Depends(get_db),
    current_admin: AppUser = Depends(get_admin_user),
):
    invoice = _svc.get_invoice(db, invoice_no)
    if invoice is None:
        raise HTTPException(status_code=404, detail="发票申请不存在")
    try:
        invoice = _svc.reject_invoice(db, invoice, reviewer=current_admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit_log(
        db, "invoice.reject",
        admin_id=int(current_admin.id),
        target_ref=invoice_no,
    )
    return {"invoice": serialize_invoice(invoice)}


# --- /admin/grant-plan -----------------------------------------------------


@router.post("/grant-plan", summary="(admin) 手动开通套餐 (§11.10 兜底)")
async def admin_grant_plan(
    body: GrantPlanRequest,
    db: Session = Depends(get_db),
    current_admin: AppUser = Depends(get_admin_user),
):
    user = db.query(AppUser).filter(AppUser.id == int(body.user_id)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="目标用户不存在")
    try:
        sub: AppSubscription = svc_grant_plan(
            db,
            user,
            plan_code=body.plan_code,
            grant_days=int(body.grant_days),
            source="admin",
            note=(body.note or f"granted by admin:{current_admin.email}")[:255],
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit_log(
        db, "admin.grant_plan",
        admin_id=int(current_admin.id),
        target_user_id=int(user.id),
        detail={
            "planCode": body.plan_code,
            "grantDays": body.grant_days,
            "note": body.note,
        },
    )
    return {
        "user": _serialize_admin_user(user),
        "subscription": {
            "id": int(sub.id),
            "planCode": sub.plan_code,
            "source": sub.source,
            "startedAt": sub.started_at.isoformat() if sub.started_at else None,
            "expiresAt": sub.expires_at.isoformat() if sub.expires_at else None,
            "note": sub.note,
        },
    }


# --- /admin/audit-logs ----------------------------------------------------


@router.get("/audit-logs", summary="(admin) 审计日志列表")
async def admin_audit_logs(
    db: Session = Depends(get_db),
    _: AppUser = Depends(get_admin_user),
    action: Optional[str] = Query(default=None),
    user_id: Optional[int] = Query(default=None, alias="userId"),
    admin_id: Optional[int] = Query(default=None, alias="adminId"),
    limit: int = Query(default=200, ge=1, le=1000),
):
    q = db.query(AppAuditLog)
    if action:
        q = q.filter(AppAuditLog.action == action)
    if user_id is not None:
        q = q.filter(AppAuditLog.user_id == user_id)
    if admin_id is not None:
        q = q.filter(AppAuditLog.admin_id == admin_id)
    rows = q.order_by(AppAuditLog.created_at.desc()).limit(limit).all()
    return {"logs": [serialize_audit_log(r) for r in rows], "count": len(rows)}


# --- /admin/stats ----------------------------------------------------------


@router.get("/stats", summary="(admin) 基础聚合指标")
async def admin_stats(
    db: Session = Depends(get_db),
    _: AppUser = Depends(get_admin_user),
):
    """简单聚合, 不接 BI; 用于运营后台首屏概览。"""
    total_users = db.query(func.count(AppUser.id)).scalar() or 0
    paid_users = (
        db.query(func.count(AppUser.id))
        .filter(AppUser.plan_code != "free")
        .filter((AppUser.plan_expires_at.is_(None)) | (AppUser.plan_expires_at > datetime.utcnow()))
        .scalar()
        or 0
    )
    orders_total = db.query(func.count(AppOrder.id)).scalar() or 0
    orders_paid = (
        db.query(func.count(AppOrder.id)).filter(AppOrder.status == "paid").scalar() or 0
    )
    pending_refunds = (
        db.query(func.count(AppRefund.id))
        .filter(AppRefund.status == "pending")
        .scalar()
        or 0
    )
    pending_invoices = (
        db.query(func.count(AppInvoice.id))
        .filter(AppInvoice.status == "pending")
        .scalar()
        or 0
    )
    revenue_cents = (
        db.query(func.coalesce(func.sum(AppOrder.amount_cents), 0))
        .filter(AppOrder.status == "paid")
        .scalar()
        or 0
    )
    return {
        "users": {"total": int(total_users), "paid": int(paid_users)},
        "orders": {
            "total": int(orders_total),
            "paid": int(orders_paid),
            "revenueCents": int(revenue_cents),
        },
        "pending": {
            "refunds": int(pending_refunds),
            "invoices": int(pending_invoices),
        },
    }
