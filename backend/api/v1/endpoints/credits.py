# -*- coding: utf-8 -*-
"""积分包购买相关的 HTTP 路由。

挂载路径前缀为 ``/api/v1/credits/*``。与 ``/billing`` 订阅端点刻意分开：
本组路由只负责余额充值（top-up）。本层负责支付模式开关、审计日志与支付
通道对接，订单创建与履约规则放在 ``CreditOrderService`` 中。
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
    """判断某个环境变量功能开关是否为真值。"""
    return os.environ.get(name, "false").lower() in ("1", "true", "yes")


def _payment_enabled(db: Session) -> bool:
    """读取平台开关，决定是否使用真实支付通道。"""
    return bool(get_platform_setting_value(db, "PAYMENT_ENABLED"))


def _payment_mock_enabled() -> bool:
    """判断本地 mock 支付端点是否启用。"""
    return _flag("PAYMENT_MOCK_ENABLED")


def _order_expire_minutes(db: Session) -> int:
    """读取平台设置中的订单过期时长（分钟）。"""
    return int(get_platform_setting_value(db, "ORDER_EXPIRE_MINUTES"))


@router.get("/packages", summary="列出可购买积分包")
async def list_credit_packages(db: Session = Depends(get_db)):
    """列出当前可购买的积分包。"""
    packages = _svc.list_packages(db)
    return {"packages": [serialize_credit_package(p) for p in packages]}


@router.post("/orders", summary="创建积分购买订单")
async def create_credit_order(
    request: Request,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    body: dict = Body(...),
):
    """为当前用户创建一笔积分充值订单。"""
    package_code = body.get("packageCode") or ""
    provider = body.get("provider") or "manual"
    if not package_code:
        raise HTTPException(status_code=422, detail="packageCode 不能为空")
    if provider not in ("wechat", "alipay", "manual"):
        raise HTTPException(status_code=422, detail="provider 不合法")

    # 记录客户端 IP / UA，便于审计和反作弊
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
        # 业务校验错误（如套餐已下架、优惠码无效）统一以 400 返回
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
    """列出当前用户的所有积分充值订单。"""
    orders = _svc.list_orders(db, user_id=current_user.id)
    return {"orders": [serialize_credit_order(o) for o in orders]}


@router.get("/orders/{order_no}", summary="查询积分订单")
async def get_credit_order(
    order_no: str,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    """查询属于当前用户的某一笔积分订单。"""
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
    """对一笔积分订单发起支付：根据平台开关走真实通道或 mock 通道。"""
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="积分订单不存在")
    if order.status not in ("created", "pending"):
        raise HTTPException(status_code=400, detail=f"订单状态 '{order.status}' 无法发起支付")

    # created -> pending 状态迁移失败不应阻塞用户继续走支付流程
    if order.status == "created":
        try:
            order = _svc.mark_pending(db, order)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mark credit order pending failed order=%s: %s", order_no, exc)

    if _payment_enabled(db):
        # 真实支付通道：根据订单 provider 选择对应网关并下单
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
        # mock 模式：直接返回一个 dsa-mock:// 协议的占位 URL，便于本地联调
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
    """在本地 mock 支付模式下完成一笔积分订单的履约。"""
    if not _payment_mock_enabled():
        raise HTTPException(status_code=403, detail="mock-pay 端点仅在 PAYMENT_MOCK_ENABLED=true 时可用")
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="积分订单不存在")
    # 幂等保护：已支付订单直接返回，不重复履约
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
    """取消属于当前用户的、可取消状态的积分订单。"""
    order = _svc.get_order(db, order_no, user_id=current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="积分订单不存在")
    try:
        order = _svc.cancel_order(db, order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"order": serialize_credit_order(order)}
