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
- ``GET /admin/plans`` / ``PUT /admin/plans/{plan_code}``: 套餐与每日用量配置
- ``GET /admin/stats``: 简单聚合指标

后台 endpoint 直接面向运营人员，负责用户管理、订单/退款/发票处理、套餐配置、
平台开关、审计日志和聚合指标。业务动作仍委托 users/billing 服务层完成。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import get_admin_user, get_db
from src.storage import (
    AppAuditLog,
    AppInvoice,
    AppOrder,
    AppPlan,
    AppRefund,
    AppSubscription,
    AppUser,
    AppUserReferral,
)
from src.users.audit import serialize_audit_log, write_audit_log
from src.users.credits import REASON_ADMIN_ADJUST, add_credits, consume_credits
from src.users.email import EmailMessageDTO, get_email_backend
from src.services.billing import OrderService
from src.services.billing.order_service import (
    serialize_invoice,
    serialize_order,
    serialize_refund,
)
from src.users.plans import (
    grant_plan as svc_grant_plan,
    list_plan_catalog,
    serialize_plan_row,
)
from src.users.platform_settings import (
    serialize_platform_settings,
    upsert_platform_settings,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_svc = OrderService()


# --- Schemas ---------------------------------------------------------------


class ApproveRefundRequest(BaseModel):
    """管理员审核通过退款时可选的三方退款单号。"""

    model_config = {"populate_by_name": True}
    provider_refund_no: Optional[str] = Field(default=None, alias="providerRefundNo")


class RejectRefundRequest(BaseModel):
    """管理员拒绝退款时填写的备注。"""

    note: Optional[str] = Field(default=None)


class IssueInvoiceRequest(BaseModel):
    """管理员标记发票已开具时可选的发票访问链接。"""

    model_config = {"populate_by_name": True}
    issued_url: Optional[str] = Field(default=None, alias="issuedUrl")


class GrantPlanRequest(BaseModel):
    """管理员手动为用户开通套餐的请求体。"""

    model_config = {"populate_by_name": True}
    user_email: Optional[str] = Field(default=None, alias="userEmail")
    user_id: Optional[int] = Field(default=None, alias="userId")
    plan_code: str = Field(..., alias="planCode")
    grant_days: int = Field(..., alias="grantDays")
    note: Optional[str] = Field(default=None)


class UpsertPlanRequest(BaseModel):
    """管理员创建或更新套餐配置的请求体。"""

    model_config = {"populate_by_name": True}
    name: str = Field(..., min_length=1, max_length=64)
    daily_analysis_limit: int = Field(..., alias="dailyAnalysisLimit", ge=0)
    daily_agent_limit: int = Field(..., alias="dailyAgentLimit", ge=0)
    max_stocks: int = Field(..., alias="maxStocks", ge=0)
    can_webhook: bool = Field(default=False, alias="canWebhook")
    price_cents: int = Field(default=0, alias="priceCents", ge=0)
    currency: str = Field(default="CNY", min_length=1, max_length=8)
    is_active: bool = Field(default=True, alias="isActive")


class PlatformSettingUpdate(BaseModel):
    """单项运营平台配置更新。"""

    key: str = Field(..., min_length=1, max_length=64)
    value: Any


class PlatformSettingsUpdateRequest(BaseModel):
    """批量保存运营平台配置的请求体。"""

    settings: list[PlatformSettingUpdate] = Field(default_factory=list)


class AdjustCreditsRequest(BaseModel):
    """管理员手动调整用户积分的请求体。"""

    delta: int
    note: Optional[str] = Field(default=None, max_length=255)


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
        # 邮件发送失败不影响审核结果，仅记录异常便于后续排障
        logger.exception("退款通知邮件发送失败 refund_no=%s", refund.refund_no)


def _serialize_admin_user(user: AppUser) -> dict:
    """把用户行序列化为后台表格形态，避免泄漏密码/会话等敏感字段。"""
    return {
        "id": int(user.id),
        "email": user.email,
        "plan": user.plan_code,
        "planExpiresAt": user.plan_expires_at.isoformat() if user.plan_expires_at else None,
        "creditBalance": int(getattr(user, "credit_balance", 0) or 0),
        "referralCode": getattr(user, "referral_code", None),
        "isAdmin": bool(getattr(user, "is_admin", False)),
        "isResearchOperator": bool(getattr(user, "is_research_operator", False)),
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "lastLoginAt": user.last_login_at.isoformat() if user.last_login_at else None,
        "termsVersion": getattr(user, "terms_version", None),
        "status": user.status,
    }


def _normalize_plan_code(plan_code: str) -> str:
    """规范化并校验套餐代码，避免 URL 中传入非法字符。"""
    code = (plan_code or "").strip().lower()
    if not code or len(code) > 32:
        raise HTTPException(status_code=422, detail="套餐代码不合法")
    if not all(ch.isalnum() or ch in ("_", "-") for ch in code):
        raise HTTPException(status_code=422, detail="套餐代码只能包含字母、数字、下划线或连字符")
    return code


# --- /admin/me -------------------------------------------------------------


@router.get("/me", summary="(admin) 当前 admin 信息 + 心跳")
async def admin_me(current_admin: AppUser = Depends(get_admin_user)):
    """返回当前 admin 的身份信息，供后台控制台做心跳与权限校验。"""
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
    """按邮箱/套餐/角色筛选用户列表。"""
    q = db.query(AppUser)
    if email_like:
        q = q.filter(AppUser.email.ilike(f"%{email_like.strip()}%"))
    if plan_code:
        q = q.filter(AppUser.plan_code == plan_code)
    if is_admin is not None:
        q = q.filter(AppUser.is_admin.is_(bool(is_admin)))
    rows = q.order_by(AppUser.created_at.desc()).limit(limit).all()
    return {"users": [_serialize_admin_user(u) for u in rows], "count": len(rows)}


@router.post("/users/{user_id}/credits/adjust", summary="(admin) 手动调整用户积分")
async def admin_adjust_user_credits(
    user_id: int,
    body: AdjustCreditsRequest,
    db: Session = Depends(get_db),
    current_admin: AppUser = Depends(get_admin_user),
):
    """调整指定用户的积分余额，并写入审计记录。"""
    user = db.query(AppUser).filter(AppUser.id == int(user_id)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="目标用户不存在")
    delta = int(body.delta)
    if delta == 0:
        raise HTTPException(status_code=422, detail="调整积分不能为 0")
    try:
        if delta > 0:
            # 正向调整走 add_credits，负向走 consume_credits 以复用既有账本逻辑
            add_credits(
                db,
                user=user,
                amount=delta,
                reason=REASON_ADMIN_ADJUST,
                related_type="admin",
                related_id=str(current_admin.id),
                note=body.note or f"adjusted by {current_admin.email}",
            )
        else:
            consume_credits(
                db,
                user=user,
                amount=abs(delta),
                kind="admin",
                reason=REASON_ADMIN_ADJUST,
                related_type="admin",
                related_id=str(current_admin.id),
                note=body.note or f"adjusted by {current_admin.email}",
            )
        db.commit()
        db.refresh(user)
    except ValueError as exc:
        # 余额不足等业务校验错误回滚后转 400，由前端展示
        db.rollback()
        raise HTTPException(status_code=400, detail="用户积分余额不足，无法扣减") from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    write_audit_log(
        db,
        "admin.credits.adjust",
        admin_id=int(current_admin.id),
        target_user_id=int(user.id),
        detail={"delta": delta, "note": body.note},
    )
    return {"user": _serialize_admin_user(user)}


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
    """按状态/用户/支付通道筛选订单列表。"""
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
    """根据订单号获取单条订单，不存在时返回 404。"""
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
    """按状态筛选退款申请列表，便于运营跟进。"""
    refunds = _svc.list_refunds_admin(db, status=status, limit=limit)
    return {"refunds": [serialize_refund(r) for r in refunds], "count": len(refunds)}


@router.post("/refunds/{refund_no}/approve", summary="(admin) 审核通过退款")
async def admin_approve_refund(
    refund_no: str,
    db: Session = Depends(get_db),
    current_admin: AppUser = Depends(get_admin_user),
    body: ApproveRefundRequest = Body(default=ApproveRefundRequest()),
):
    """审核通过退款：落库 + 审计 + 尽力通知用户。"""
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
    """审核拒绝退款：落库 + 审计 + 尽力通知用户。"""
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
    """按状态筛选发票申请列表。"""
    invoices = _svc.list_invoices_admin(db, status=status, limit=limit)
    return {"invoices": [serialize_invoice(i) for i in invoices], "count": len(invoices)}


@router.post("/invoices/{invoice_no}/issue", summary="(admin) 标记发票已开具")
async def admin_issue_invoice(
    invoice_no: str,
    db: Session = Depends(get_db),
    current_admin: AppUser = Depends(get_admin_user),
    body: IssueInvoiceRequest = Body(default=IssueInvoiceRequest()),
):
    """将发票申请标记为已开具并记录访问链接与审计。"""
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
    """拒绝一张发票申请并记录审计。"""
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
    """为指定用户开通或延长套餐，作为活动/KOL/客服补单的兜底入口。"""
    user: Optional[AppUser] = None
    user_email = (body.user_email or "").strip().lower()
    if user_email:
        user = db.query(AppUser).filter(AppUser.email == user_email).first()
    elif body.user_id is not None:
        user = db.query(AppUser).filter(AppUser.id == int(body.user_id)).first()
    else:
        raise HTTPException(status_code=422, detail="请输入目标用户邮箱")
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
            "userEmail": user.email,
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


# --- /admin/plans ----------------------------------------------------------


@router.get("/plans", summary="(admin) 套餐与每日用量配置")
async def admin_list_plans(
    db: Session = Depends(get_db),
    _: AppUser = Depends(get_admin_user),
):
    """返回所有套餐目录行（含已停用的），便于后台管理。"""
    plans = list_plan_catalog(
        db,
        include_inactive=True,
        include_allowed_models=True,
    )
    return {"plans": plans, "count": len(plans)}


@router.put("/plans/{plan_code}", summary="(admin) 保存套餐与每日用量配置")
async def admin_upsert_plan(
    body: UpsertPlanRequest,
    plan_code: str = Path(...),
    db: Session = Depends(get_db),
    current_admin: AppUser = Depends(get_admin_user),
):
    """创建或更新一个套餐，同时保留对 free 套餐的特殊保护。"""
    code = _normalize_plan_code(plan_code)
    row = db.query(AppPlan).filter(AppPlan.code == code).first()
    if row is None:
        row = AppPlan(code=code)

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="套餐名称不能为空")

    row.name = name
    row.daily_analysis_limit = int(body.daily_analysis_limit)
    row.daily_agent_limit = int(body.daily_agent_limit)
    row.max_stocks = int(body.max_stocks)
    # free 套餐不开放 webhook，价格强制 0，避免后台误改破坏默认体验
    row.can_webhook = False if code == "free" else bool(body.can_webhook)
    row.price_cents = 0 if code == "free" else int(body.price_cents)
    row.currency = (body.currency or "CNY").strip().upper()[:8] or "CNY"
    row.is_active = True if code == "free" else bool(body.is_active)
    db.add(row)

    try:
        db.commit()
        db.refresh(row)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    write_audit_log(
        db,
        "admin.plan.upsert",
        admin_id=int(current_admin.id),
        target_ref=code,
        detail={
            "name": row.name,
            "dailyAnalysisLimit": row.daily_analysis_limit,
            "dailyAgentLimit": row.daily_agent_limit,
            "maxStocks": row.max_stocks,
            "canWebhook": row.can_webhook,
            "priceCents": row.price_cents,
            "currency": row.currency,
            "isActive": row.is_active,
        },
    )
    return {"plan": serialize_plan_row(row, include_allowed_models=True)}


# --- /admin/platform-settings ---------------------------------------------


@router.get("/platform-settings", summary="(admin) To C 运营平台配置")
async def admin_list_platform_settings(
    db: Session = Depends(get_db),
    _: AppUser = Depends(get_admin_user),
):
    """返回序列化后的 To C 运营平台配置项。"""
    settings = serialize_platform_settings(db)
    return {"settings": settings, "count": len(settings)}


@router.put("/platform-settings", summary="(admin) 保存 To C 运营平台配置")
async def admin_update_platform_settings(
    body: PlatformSettingsUpdateRequest,
    db: Session = Depends(get_db),
    current_admin: AppUser = Depends(get_admin_user),
):
    """批量 upsert 平台配置，并审计发生变更的 key 列表。"""
    if not body.settings:
        raise HTTPException(status_code=422, detail="settings 不能为空")
    payload = [{"key": item.key, "value": item.value} for item in body.settings]
    try:
        settings = upsert_platform_settings(db, payload, admin_id=int(current_admin.id))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    write_audit_log(
        db,
        "admin.platform_setting.update",
        admin_id=int(current_admin.id),
        detail={"keys": [item.key for item in body.settings]},
    )
    return {"settings": settings, "count": len(settings)}


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
    """按 action / 用户 / admin 过滤审计日志，倒序返回最近记录。"""
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
    """简单聚合指标，用于后台首页概览；不接入 BI。"""
    total_users = db.query(func.count(AppUser.id)).scalar() or 0
    # 付费用户：套餐非 free 且未过期（无到期视为永久）
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
    credit_balance_total = (
        db.query(func.coalesce(func.sum(AppUser.credit_balance), 0)).scalar() or 0
    )
    referral_count = db.query(func.count(AppUserReferral.id)).scalar() or 0
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
        "credits": {
            "balanceTotal": int(credit_balance_total),
            "referrals": int(referral_count),
        },
    }
