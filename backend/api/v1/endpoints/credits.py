# -*- coding: utf-8 -*-
"""Credit package purchase endpoints.

Mounted at ``/api/v1/credits/*``. This is intentionally separate from
``/billing`` subscription endpoints: credit purchases top up balance only.
The endpoint layer coordinates payment-mode flags, audit logging, and gateway
handoff; order creation and fulfillment rules stay in ``CreditOrderService``.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.deps import get_current_user, get_db
from src.services.billing import CreditOrderService
from src.services.billing.credit_order_service import (
    serialize_credit_order,
    serialize_credit_package,
)
from src.services.billing.gateways import get_gateway
from src.users.audit import write_audit_log
from src.users.platform_settings import get_platform_setting_value
from src.storage import AppUser


logger = logging.getLogger(__name__)
router = APIRouter()
_svc = CreditOrderService()


def _flag(name: str) -> bool:
    """Return whether an environment feature flag is truthy."""
    return os.environ.get(name, "false").lower() in ("1", "true", "yes")


def _payment_enabled(db: Session) -> bool:
    """Read platform switch for real payment gateway usage."""
    return bool(get_platform_setting_value(db, "PAYMENT_ENABLED"))


def _payment_mock_enabled() -> bool:
    """Return whether local mock payment endpoints are enabled."""
    return _flag("PAYMENT_MOCK_ENABLED")


def _order_expire_minutes(db: Session) -> int:
    """Read order expiration duration from platform settings."""
    return int(get_platform_setting_value(db, "ORDER_EXPIRE_MINUTES"))


@router.get("/packages", summary="列出可购买积分包")
async def list_credit_packages(db: Session = Depends(get_db)):
    """List active credit packages available for purchase."""
    packages = _svc.list_packages(db)
    return {"packages": [serialize_credit_package(p) for p in packages]}


@router.post("/orders", summary="创建积分购买订单")
async def create_credit_order(
    request: Request,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    body: dict = Body(...),
):
    """Create a credit top-up order for the current user."""
    package_code = body.get("packageCode") or ""
    provider = body.get("provider") or "manual"
    if not package_code:
        raise HTTPException(status_code=422, detail="packageCode 不能为空")
    if provider not in ("wechat", "alipay", "manual"):
        raise HTTPException(status_code=422, detail="provider 不合法")

    client_ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    try:
        order = _svc.create_order(
            db=db,
            user=current_user,
            package_code=package_code,
            provider=provider,
            client_ip=client_ip,
            user_agent=ua,
            coupon_code=body.get("couponCode"),
            expire_minutes=_order_expire_minutes(db),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    write_audit_log(
        db,
        "credit_order.create",
        user_id=int(current_user.id),
        target_ref=order.order_no,
        detail={
            "packageCode": package_code,
            "provider": provider,
            "amountCents": order.amount_cents,
            "creditAmount": order.credit_amount,
        },
        ip=client_ip,
        user_agent=ua,
    )
    return {"order": serialize_credit_order(order)}


@router.get("/orders", summary="列出我的积分订单")
async def list_credit_orders(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """List current user's credit purchase orders."""
    orders = _svc.list_orders(db, user_id=current_user.id)
    return {"orders": [serialize_credit_order(o) for o in orders]}


@router.get("/orders/{order_no}", summary="查询积分订单")
async def get_credit_order(
    order_no: str,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Return one credit order owned by the current user."""
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="积分订单不存在")
    return {"order": serialize_credit_order(order)}


@router.post("/orders/{order_no}/pay", summary="发起积分订单支付")
async def pay_credit_order(
    order_no: str,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Start payment for a credit order through real or mock gateway mode."""
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="积分订单不存在")
    if order.status not in ("created", "pending"):
        raise HTTPException(status_code=400, detail=f"订单状态 '{order.status}' 无法发起支付")

    if order.status == "created":
        try:
            order = _svc.mark_pending(db, order)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mark credit order pending failed order=%s: %s", order_no, exc)

    if _payment_enabled(db):
        gateway = get_gateway(order.provider, db=db)
        if gateway is None:
            raise HTTPException(status_code=503, detail=f"支付通道 '{order.provider}' 未配置")
        try:
            code_url = gateway.place_order(order)
        except Exception as exc:  # noqa: BLE001
            logger.exception("credit place_order failed order=%s provider=%s", order_no, order.provider)
            raise HTTPException(status_code=502, detail=f"发起支付失败: {exc}") from exc
        return {
            "provider": order.provider,
            "codeUrl": code_url,
            "expiresAt": order.expires_at.isoformat() if order.expires_at else None,
            "mock": False,
        }

    if _payment_mock_enabled():
        provider = order.provider or "wechat"
        return {
            "provider": provider,
            "codeUrl": f"dsa-mock://credit-pay?order={order.order_no}&provider={provider}",
            "expiresAt": order.expires_at.isoformat() if order.expires_at else None,
            "mock": True,
            "hint": "Mock 模式: POST /api/v1/credits/orders/{order_no}/mock-pay 可模拟积分支付成功。",
        }

    raise HTTPException(
        status_code=503,
        detail="当前支付通道未启用；如需本地联调，请设置 PAYMENT_MOCK_ENABLED=true。",
    )


@router.post("/orders/{order_no}/mock-pay", summary="(仅 mock 模式) 模拟积分支付成功")
async def mock_pay_credit_order(
    order_no: str,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Fulfill a credit order in local mock-payment mode only."""
    if not _payment_mock_enabled():
        raise HTTPException(status_code=403, detail="mock-pay 端点仅在 PAYMENT_MOCK_ENABLED=true 时可用")
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="积分订单不存在")
    if order.status == "paid":
        return {"order": serialize_credit_order(order), "alreadyPaid": True}
    if order.status not in ("created", "pending"):
        raise HTTPException(status_code=400, detail=f"订单状态 '{order.status}' 无法 mock fulfill")
    if order.status == "created":
        order = _svc.mark_pending(db, order)
    order = _svc.fulfill_order(db, order, provider_trade_no=f"MOCK_{order.order_no}")
    return {"order": serialize_credit_order(order), "alreadyPaid": False}


@router.post("/orders/{order_no}/cancel", summary="取消积分订单")
async def cancel_credit_order(
    order_no: str,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """Cancel a cancellable credit order owned by the current user."""
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="积分订单不存在")
    try:
        order = _svc.cancel_order(db, order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"order": serialize_credit_order(order)}
