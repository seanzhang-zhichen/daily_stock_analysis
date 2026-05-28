# -*- coding: utf-8 -*-
"""Billing endpoints (Phase 2 + Phase 5).

挂载位置: ``/api/v1/billing/*``。

Phase 2 (只读):
  GET  /plans        - 套餐目录（无需登录）
  GET  /subscription - 当前用户订阅状态

Phase 5 (订单 / 回调 / 退款 / 发票):
  POST   /orders                         - 创建订单
  GET    /orders/{order_no}              - 查询订单
  POST   /orders/{order_no}/pay          - 发起支付（返回二维码/跳转 URL）
  POST   /orders/{order_no}/cancel       - 用户取消订单
  POST   /callbacks/wechat               - 微信支付回调
  POST   /callbacks/alipay               - 支付宝回调
  POST   /refunds                        - 申请退款
  GET    /refunds/{refund_no}            - 查询退款
  POST   /invoices                       - 申请发票
  GET    /invoices                       - 列出我的发票

当 ``PAYMENT_ENABLED=false`` 时（默认），/pay 端点返回 503 并提示使用人工汇款兜底。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Body
from sqlalchemy.orm import Session

from api.deps import get_current_user, get_db
from src.storage import AppOrder, AppRefund, AppInvoice, AppSubscription, AppUser
from src.users.config import SESSION_COOKIE_NAME
from src.users.plans import list_plan_catalog, resolve_user_plan
from src.users.sessions import resolve_session
from src.services.billing import OrderService
from src.services.billing.gateways import get_gateway
from src.services.billing.security import check_callback_ip, record_sig_failure
from src.users.audit import write_audit_log
from src.users.platform_settings import get_platform_setting_value


logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize_subscription(row: AppSubscription) -> dict:
    return {
        "id": int(row.id),
        "planCode": row.plan_code,
        "source": row.source,
        "startedAt": row.started_at.isoformat() if row.started_at else None,
        "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
        "note": row.note,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _resolve_request_user(request: Request, db: Session) -> Optional[AppUser]:
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie_value:
        return None
    return resolve_session(db, cookie_value)


@router.get("/plans", summary="列出当前可见套餐目录")
async def billing_plans(request: Request, db: Session = Depends(get_db)):
    """套餐目录, 不需要登录即可访问 (用于落地页 / 注册流引导)。"""
    plans = list_plan_catalog(db)

    user = _resolve_request_user(request, db)
    current_plan_payload = None
    if user is not None:
        plan = resolve_user_plan(db, user)
        current_plan_payload = {
            "code": plan.code,
            "name": plan.name,
            "isPro": plan.is_pro,
            "expiresAt": plan.expires_at.isoformat() if plan.expires_at else None,
        }

    return {
        "userModeEnabled": True,
        "plans": plans,
        "currentPlan": current_plan_payload,
    }


@router.get("/subscription", summary="查询当前用户的订阅状态 + 历史")
async def billing_subscription(
    request: Request,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    plan = resolve_user_plan(db, current_user)
    history_rows = (
        db.query(AppSubscription)
        .filter(AppSubscription.user_id == current_user.id)
        .order_by(AppSubscription.started_at.desc())
        .limit(50)
        .all()
    )

    now = datetime.utcnow()
    is_active_paid = (
        plan.code != "free"
        and plan.expires_at is not None
        and plan.expires_at > now
    )

    return {
        "plan": {
            "code": plan.code,
            "name": plan.name,
            "isPro": plan.is_pro,
            "dailyAnalysisLimit": plan.daily_analysis_limit,
            "dailyAgentLimit": plan.daily_agent_limit,
            "maxStocks": plan.max_stocks,
            "canWebhook": plan.can_webhook,
            "expiresAt": plan.expires_at.isoformat() if plan.expires_at else None,
            "isActivePaid": is_active_paid,
        },
        "subscriptions": [_serialize_subscription(r) for r in history_rows],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 5 — 订单 / 支付 / 回调 / 退款 / 发票
# ═══════════════════════════════════════════════════════════════════════════════

_svc = OrderService()


def _flag(name: str) -> bool:
    return os.environ.get(name, "false").lower() in ("1", "true", "yes")


def _payment_enabled(db: Session) -> bool:
    return bool(get_platform_setting_value(db, "PAYMENT_ENABLED"))


def _order_expire_minutes(db: Session) -> int:
    return int(get_platform_setting_value(db, "ORDER_EXPIRE_MINUTES"))


def _payment_mock_enabled() -> bool:
    """``PAYMENT_MOCK_ENABLED=true`` 时允许 ``/pay`` 返回 mock code_url +
    `mock-pay` 端点手动触发 fulfill, 用于本地 / 沙箱前端联调。

    生产环境必须显式关闭 (默认 false), 防止绕过支付。
    """
    return _flag("PAYMENT_MOCK_ENABLED")


# ── 订单 ─────────────────────────────────────────────────────────────────────

@router.post("/orders", summary="创建订单")
async def create_order(
    request: Request,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    body: dict = Body(...),
):
    """用户选择套餐后创建订单；同一用户同套餐 15 分钟内未支付的订单直接复用（幂等）。


    请求体: ``{ "planCode": "pro", "provider": "wechat" }``
    """
    plan_code = body.get("planCode") or ""
    provider = body.get("provider") or "manual"
    if not plan_code:
        raise HTTPException(status_code=422, detail="planCode 不能为空")
    if provider not in ("wechat", "alipay", "manual"):
        raise HTTPException(status_code=422, detail="provider 不合法")


    client_ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")


    try:
        order = _svc.create_order(
            db=db,
            user=current_user,
            plan_code=plan_code,
            provider=provider,
            client_ip=client_ip,
            user_agent=ua,
            coupon_code=body.get("couponCode"),
            expire_minutes=_order_expire_minutes(db),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


    from src.services.billing.order_service import serialize_order
    write_audit_log(
        db, "order.create",
        user_id=int(current_user.id),
        target_ref=order.order_no,
        detail={"planCode": plan_code, "provider": provider, "amountCents": order.amount_cents},
        ip=client_ip,
        user_agent=ua,
    )
    return {"order": serialize_order(order)}


@router.get("/orders/{order_no}", summary="查询订单")
async def get_order(
    order_no: str,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    from src.services.billing.order_service import serialize_order
    return {"order": serialize_order(order)}


@router.post("/orders/{order_no}/pay", summary="发起支付（返回二维码 URL）")
async def pay_order(
    order_no: str,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """发起支付。

    返回值:
      ``{ "codeUrl": "...", "expiresAt": "...", "provider": "wechat|alipay", "mock": bool }``

    模式:
    - ``PAYMENT_ENABLED=true`` + 真实 SDK 接入: 调用通道下单, 返回真实 ``code_url``。
    - ``PAYMENT_ENABLED=false`` + ``PAYMENT_MOCK_ENABLED=true``: 返回 mock 二维码内容,
      允许通过 :meth:`mock_pay_order` 端点手动 fulfill 订单, 用于前端联调。
    - ``PAYMENT_ENABLED=false`` + 关闭 mock: 返回 503 并附人工收款兜底说明 (§11.10)。
    """
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status not in ("created", "pending"):
        raise HTTPException(status_code=400, detail=f"订单状态 '{order.status}' 无法发起支付")

    # 进入 pending 状态 (幂等)
    if order.status == "created":
        try:
            order = _svc.mark_pending(db, order)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mark_pending 失败 (order=%s): %s", order_no, exc)

    if _payment_enabled(db):
        gateway = get_gateway(order.provider, db=db)
        if gateway is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"支付通道 '{order.provider}' 未配置 (PAYMENT_ENABLED=true 但密钥缺失)。"
                    "请检查 WECHAT_PAY_* / ALIPAY_* 环境变量。"
                ),
            )
        try:
            code_url = gateway.place_order(order)
        except Exception as exc:  # noqa: BLE001
            logger.exception("place_order 失败 (order=%s provider=%s)", order_no, order.provider)
            raise HTTPException(status_code=502, detail=f"发起支付失败: {exc}") from exc
        return {
            "provider": order.provider,
            "codeUrl": code_url,
            "expiresAt": order.expires_at.isoformat() if order.expires_at else None,
            "mock": False,
        }

    if _payment_mock_enabled():
        # 仅在显式开启 mock 时返回伪二维码内容
        provider = order.provider or "wechat"
        mock_code = f"dsa-mock://pay?order={order.order_no}&provider={provider}"
        return {
            "provider": provider,
            "codeUrl": mock_code,
            "expiresAt": order.expires_at.isoformat() if order.expires_at else None,
            "mock": True,
            "hint": "Mock 模式: 在 /account/orders 页面或 POST /api/v1/billing/orders/{order_no}/mock-pay 手动 fulfill。",
        }

    raise HTTPException(
        status_code=503,
        detail=(
            "当前支付通道未启用 (PAYMENT_ENABLED=false)。"
            "请通过页面提示的人工收款方式完成付款后联系管理员手动开通; "
            "如需本地联调, 请设置 PAYMENT_MOCK_ENABLED=true。"
        ),
    )


@router.post("/orders/{order_no}/mock-pay", summary="(仅 mock 模式) 手动模拟支付成功")
async def mock_pay_order(
    order_no: str,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """模拟支付成功并 fulfill 订单。仅在 ``PAYMENT_MOCK_ENABLED=true`` 时可用。

    用途: 本地 / 沙箱环境下让前端轮询能拿到 ``status=paid`` 走通整条 UX。
    生产环境必须关闭 ``PAYMENT_MOCK_ENABLED`` 以防绕过付款。
    """
    if not _payment_mock_enabled():
        raise HTTPException(
            status_code=403,
            detail="mock-pay 端点仅在 PAYMENT_MOCK_ENABLED=true 时可用",
        )
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status == "paid":
        from src.services.billing.order_service import serialize_order
        return {"order": serialize_order(order), "alreadyPaid": True}
    if order.status not in ("created", "pending"):
        raise HTTPException(status_code=400, detail=f"订单状态 '{order.status}' 无法 mock fulfill")

    if order.status == "created":
        order = _svc.mark_pending(db, order)

    try:
        order = _svc.fulfill_order(db, order, provider_trade_no=f"MOCK_{order.order_no}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("mock fulfill 失败 (order=%s)", order_no)
        raise HTTPException(status_code=500, detail=f"mock fulfill 失败: {exc}") from exc

    from src.services.billing.order_service import serialize_order
    return {"order": serialize_order(order), "alreadyPaid": False}


@router.post("/orders/{order_no}/cancel", summary="用户主动取消订单")
async def cancel_order(
    order_no: str,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    try:
        order = _svc.cancel_order(db, order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from src.services.billing.order_service import serialize_order
    return {"order": serialize_order(order)}


# ── 支付通道回调 ──────────────────────────────────────────────────────────────

def _legacy_record_unverified(
    db: Session,
    provider: str,
    body_text: str,
    signature: str = "",
    event_id_hint: str = "",
) -> None:
    """Gateway 未配置时的兜底: 原样落库 ``app_payment_events`` (signature_valid=False)。

    保留这条路径是为了在密钥还没下证 / .env 还没配齐时, 仍能审计通道有没有
    在敲门; 不会驱动业务 fulfill。
    """
    event_id = event_id_hint or f"{provider}-{datetime.utcnow().timestamp()}"
    try:
        _svc.record_payment_event(
            db=db,
            order_no="unknown",
            provider=provider,
            event_type="callback.received",
            provider_event_id=event_id,
            raw_payload=body_text[:4096],
            signature=signature[:512] or None,
            signature_valid=False,
        )
    except Exception:  # noqa: BLE001
        logger.warning("legacy record_payment_event failed", exc_info=True)


@router.post("/callbacks/wechat", summary="微信支付回调（签名校验 + 驱动 fulfill）")
async def wechat_callback(request: Request, db: Session = Depends(get_db)):
    """微信支付 V3 异步通知入口。

    流程:

    1. ``get_gateway('wechat')`` 拿到 :class:`WechatGateway` (按 env 解析);
       未配置时落兜底事件 + 直接返回 SUCCESS, 不驱动业务。
    2. :meth:`WechatGateway.verify_callback` 做签名 + 时间戳 + 解密;
    3. :meth:`OrderService.process_callback` 落 ``app_payment_events`` 并按
       签名 + 金额一致性驱动 ``fulfill_order`` (幂等)。

    无论业务驱动是否命中, 回调统一返回 HTTP 200。
    """
    body_bytes = await request.body()
    raw = body_bytes.decode("utf-8", errors="replace")
    signature_hdr = request.headers.get("Wechatpay-Signature", "")

    if not check_callback_ip(request, "wechat"):
        write_audit_log(
            db, "callback.ip_blocked",
            detail={"provider": "wechat", "ip": request.headers.get("X-Real-IP") or str(getattr(request.client, 'host', ''))},
        )
        return {"code": "SUCCESS", "message": "OK"}

    gateway = get_gateway("wechat", db=db)
    if gateway is None:
        _legacy_record_unverified(
            db, "wechat", raw, signature=signature_hdr,
            event_id_hint=(
                request.headers.get("Wechatpay-Nonce", "")
                + request.headers.get("Wechatpay-Timestamp", "")
                + signature_hdr[:32]
            ) or "",
        )
        return {"code": "SUCCESS", "message": "OK"}

    result = gateway.verify_callback(dict(request.headers), body_bytes)
    outcome = _svc.process_callback(db, result, signature_raw=signature_hdr)

    if not result.signature_valid:
        record_sig_failure("wechat")

    if outcome.fulfilled:
        logger.info("wechat callback fulfilled order=%s", result.out_trade_no)
    elif outcome.reason:
        logger.info(
            "wechat callback not fulfilled order=%s reason=%s",
            result.out_trade_no, outcome.reason,
        )

    # 微信文档: 处理失败时返回非 200, 微信会重试。当前所有路径都已幂等落库,
    # 即使 signature_invalid 也返回 SUCCESS, 防止伪造攻击触发反复重试 (DDoS)。
    return {"code": "SUCCESS", "message": "OK"}


@router.post("/callbacks/alipay", summary="支付宝回调（签名校验 + 驱动 fulfill）")
async def alipay_callback(request: Request, db: Session = Depends(get_db)):
    """支付宝 PC 网站支付异步通知入口。

    流程同 :func:`wechat_callback` (gateway → verify → process_callback)。
    """
    from fastapi.responses import PlainTextResponse

    body_bytes = await request.body()
    raw = body_bytes.decode("utf-8", errors="replace")

    if not check_callback_ip(request, "alipay"):
        write_audit_log(
            db, "callback.ip_blocked",
            detail={"provider": "alipay", "ip": request.headers.get("X-Real-IP") or str(getattr(request.client, 'host', ''))},
        )
        return PlainTextResponse("success")

    gateway = get_gateway("alipay", db=db)
    if gateway is None:
        notify_id = ""
        for part in raw.split("&"):
            if part.startswith("notify_id="):
                notify_id = part[len("notify_id="):]
                break
        _legacy_record_unverified(
            db, "alipay", raw, event_id_hint=notify_id,
        )
        return PlainTextResponse("success")

    result = gateway.verify_callback(dict(request.headers), body_bytes)
    outcome = _svc.process_callback(db, result)

    if not result.signature_valid:
        record_sig_failure("alipay")

    if outcome.fulfilled:
        logger.info("alipay callback fulfilled order=%s", result.out_trade_no)
    elif outcome.reason:
        logger.info(
            "alipay callback not fulfilled order=%s reason=%s",
            result.out_trade_no, outcome.reason,
        )

    # 支付宝要求成功处理返回纯文本 "success", 失败返回 "failure"。
    # 签名失败 / 业务驱动失败时返回 success 防重试风暴, 由对账脚本兜底差异。
    return PlainTextResponse("success")


# ── 退款 ─────────────────────────────────────────────────────────────────────

@router.post("/refunds", summary="申请退款")
async def request_refund(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    body: dict = Body(...),
):
    """请求体: ``{ "orderNo": "...", "reason": "..." }``"""
    order_no = body.get("orderNo") or ""
    reason = body.get("reason") or ""
    if not order_no:
        raise HTTPException(status_code=422, detail="orderNo 不能为空")


    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")


    try:
        refund = _svc.create_refund(
            db=db,
            order=order,
            user=current_user,
            amount_cents=order.amount_cents,
            reason=reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


    from src.services.billing.order_service import serialize_refund
    write_audit_log(
        db, "refund.create",
        user_id=int(current_user.id),
        target_ref=refund.refund_no,
        detail={"orderNo": order_no, "amountCents": refund.amount_cents, "reason": reason[:200]},
    )
    return {"refund": serialize_refund(refund)}


@router.get("/refunds/{refund_no}", summary="查询退款")
async def get_refund(
    refund_no: str,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    refund = _svc.get_refund(db, refund_no, user_id=current_user.id)
    if refund is None:
        raise HTTPException(status_code=404, detail="退款记录不存在")
    from src.services.billing.order_service import serialize_refund
    return {"refund": serialize_refund(refund)}


# ── 发票 ─────────────────────────────────────────────────────────────────────

@router.post("/invoices", summary="申请发票")
async def request_invoice(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    body: dict = Body(...),
):
    """请求体: ``{ "orderNo": "...", "invoiceType": "personal"|"company", "title": "...", "email": "...", "taxId": "..." }``"""
    order_no = body.get("orderNo") or ""
    invoice_type = body.get("invoiceType") or "personal"
    title = body.get("title") or ""
    email = body.get("email") or ""
    tax_id = body.get("taxId") or None


    if not order_no:
        raise HTTPException(status_code=422, detail="orderNo 不能为空")
    if invoice_type not in ("personal", "company"):
        raise HTTPException(status_code=422, detail="invoiceType 须为 personal 或 company")
    if not title.strip():
        raise HTTPException(status_code=422, detail="发票抬头不能为空")
    if not email.strip():
        raise HTTPException(status_code=422, detail="收件邮箱不能为空")
    if invoice_type == "company" and not (tax_id or "").strip():
        raise HTTPException(status_code=422, detail="企业发票须填写税号")


    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")


    try:
        invoice = _svc.create_invoice(
            db=db,
            order=order,
            user=current_user,
            invoice_type=invoice_type,
            title=title.strip(),
            email=email.strip(),
            tax_id=tax_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


    from src.services.billing.order_service import serialize_invoice
    return {"invoice": serialize_invoice(invoice)}


@router.get("/invoices", summary="列出我的发票申请")
async def list_invoices(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    invoices = _svc.list_invoices(db, user_id=current_user.id)
    from src.services.billing.order_service import serialize_invoice
    return {"invoices": [serialize_invoice(i) for i in invoices]}


@router.get("/orders", summary="列出我的订单")
async def list_orders(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    orders = _svc.list_orders(db, user_id=current_user.id)
    from src.services.billing.order_service import serialize_order
    return {"orders": [serialize_order(o) for o in orders]}
