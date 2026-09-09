# -*- coding: utf-8 -*-
"""积分套餐订购与履约服务。

积分购买与订阅计费共用支付网关，但目录、订单表、回调账本与
履约语义彼此独立，因此单独成模块管理。
"""

from __future__ import annotations

import json
import logging
import random
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from src.storage import (
    AppCreditOrder,
    AppCreditPackage,
    AppCreditPaymentEvent,
    AppUser,
)
from src.services.billing.gateways import CallbackResult
from src.users.credits import grant_credit_purchase


logger = logging.getLogger(__name__)

# 允许的状态流转白名单：(当前状态, 目标状态)。
# 未列出的流转一律拒绝，防止已支付订单被回滚或重复支付
VALID_CREDIT_ORDER_TRANSITIONS: set[tuple[str, str]] = {
    ("created", "pending"),
    ("created", "closed"),
    ("pending", "paid"),
    ("pending", "failed"),
    ("pending", "closed"),
    ("paid", "refunded"),
    ("paid", "partial_refunded"),
}

CREDIT_ORDER_EXPIRE_MINUTES = 15


class InvalidCreditOrderTransitionError(ValueError):
    """积分订单试图做非法状态流转时抛出。"""


def _assert_credit_transition(current: str, new: str) -> None:
    """在积分订单状态流转白名单内做合法性校验，越权时抛 InvalidCreditOrderTransitionError。"""
    if (current, new) not in VALID_CREDIT_ORDER_TRANSITIONS:
        raise InvalidCreditOrderTransitionError(
            f"credit order status cannot change from {current!r} to {new!r}"
        )


def _gen_credit_order_no() -> str:
    """生成可读的积分订单号：``DSAC`` + 日期 + 10 位随机大写字母数字。"""
    today = datetime.now().strftime("%Y%m%d")
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    return f"DSAC{today}{suffix}"


def serialize_credit_package(package: AppCreditPackage) -> dict:
    """把积分套餐目录行序列化为 API 响应字典。"""
    return {
        "code": package.code,
        "name": package.name,
        "creditAmount": int(package.credit_amount or 0),
        "priceCents": int(package.price_cents or 0),
        "currency": package.currency or "CNY",
        "isActive": bool(package.is_active),
        "sortOrder": int(package.sort_order or 100),
    }


def serialize_credit_order(order: AppCreditOrder) -> dict:
    """序列化积分订单行，不暴露内部账本（ledger）细节。"""
    return {
        "orderNo": order.order_no,
        "packageCode": order.package_code,
        "creditAmount": int(order.credit_amount or 0),
        "amountCents": int(order.amount_cents or 0),
        "originalAmountCents": int(order.original_amount_cents or 0),
        "discountCents": int(order.discount_cents or 0),
        "couponCode": order.coupon_code,
        "currency": order.currency,
        "provider": order.provider,
        "status": order.status,
        "paidAt": order.paid_at.isoformat() if order.paid_at else None,
        "expiresAt": order.expires_at.isoformat() if order.expires_at else None,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
        "updatedAt": order.updated_at.isoformat() if order.updated_at else None,
    }


@dataclass
class CreditCallbackOutcome:
    """积分支付回调的结构化处理结果。

    Attributes:
        event: 已落库的回调事件记录。
        fulfilled: 本次是否真正完成了积分发放。
        already_processed: 该事件此前是否已处理过（幂等命中）。
        reason: 未履约的原因，如 signature_invalid / amount_mismatch。
    """

    event: AppCreditPaymentEvent
    fulfilled: bool = False
    already_processed: bool = False
    reason: Optional[str] = None


class CreditOrderService:
    """管理积分套餐订单与支付履约（幂等）。"""

    def list_packages(self, db: Session, *, include_inactive: bool = False) -> List[AppCreditPackage]:
        """列出积分套餐；默认只返回上架的，按排序号与价格升序。"""
        q = db.query(AppCreditPackage)
        if not include_inactive:
            q = q.filter(AppCreditPackage.is_active.is_(True))
        return q.order_by(AppCreditPackage.sort_order.asc(), AppCreditPackage.price_cents.asc()).all()

    def get_package(self, db: Session, package_code: str) -> Optional[AppCreditPackage]:
        """按 code 返回单个上架套餐，不存在或未上架时返回 None。"""
        return (
            db.query(AppCreditPackage)
            .filter(AppCreditPackage.code == package_code, AppCreditPackage.is_active.is_(True))
            .first()
        )

    def create_order(
        self,
        db: Session,
        user: AppUser,
        package_code: str,
        provider: str,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        coupon_code: Optional[str] = None,
        expire_minutes: int = CREDIT_ORDER_EXPIRE_MINUTES,
    ) -> AppCreditOrder:
        """为同一用户/套餐复用或新建一笔未过期的积分订单。"""
        now = datetime.utcnow()
        existing = (
            db.query(AppCreditOrder)
            .filter(
                AppCreditOrder.user_id == user.id,
                AppCreditOrder.package_code == package_code,
                AppCreditOrder.status.in_(["created", "pending"]),
                AppCreditOrder.expires_at > now,
            )
            .order_by(AppCreditOrder.created_at.desc())
            .first()
        )
        if existing:
            return existing

        package = self.get_package(db, package_code)
        if package is None:
            raise ValueError(f"credit package {package_code!r} does not exist or is inactive")
        credit_amount = int(package.credit_amount or 0)
        if credit_amount <= 0:
            raise ValueError("credit package must have a positive credit amount")

        amount = int(package.price_cents or 0)
        # 优惠券抵扣尚未实现，discount 恒为 0；coupon_code 仅落库留待后续对账
        discount = 0
        if coupon_code:
            pass

        # 下单时冻结价格快照，后续套餐调价不影响已创建订单的金额
        quote = {
            "packageCode": package.code,
            "packageName": package.name,
            "creditAmount": credit_amount,
            "priceCents": package.price_cents,
            "currency": package.currency,
            "capturedAt": now.isoformat(),
        }

        order = AppCreditOrder(
            order_no=_gen_credit_order_no(),
            user_id=user.id,
            package_code=package.code,
            credit_amount=credit_amount,
            amount_cents=max(0, amount - discount),
            original_amount_cents=amount,
            discount_cents=discount,
            coupon_code=coupon_code,
            currency=package.currency or "CNY",
            provider=provider,
            status="created",
            client_ip=client_ip,
            user_agent=user_agent,
            quote_snapshot=json.dumps(quote, ensure_ascii=False),
            expires_at=now + timedelta(minutes=int(expire_minutes)),
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        logger.info("credit order created: %s user=%d package=%s amount=%d", order.order_no, user.id, package.code, order.amount_cents)
        return order

    def get_order(self, db: Session, order_no: str, user_id: Optional[int] = None) -> Optional[AppCreditOrder]:
        """按订单号查询积分订单；传入 user_id 时额外限定归属，防止越权访问。"""
        q = db.query(AppCreditOrder).filter(AppCreditOrder.order_no == order_no)
        if user_id is not None:
            q = q.filter(AppCreditOrder.user_id == user_id)
        return q.first()

    def list_orders(self, db: Session, user_id: int, limit: int = 50) -> List[AppCreditOrder]:
        """列出指定用户最近的积分订单。"""
        return (
            db.query(AppCreditOrder)
            .filter(AppCreditOrder.user_id == user_id)
            .order_by(AppCreditOrder.created_at.desc())
            .limit(limit)
            .all()
        )

    def mark_pending(self, db: Session, order: AppCreditOrder) -> AppCreditOrder:
        """把刚创建的积分订单置为待支付（pending）状态。"""
        _assert_credit_transition(order.status, "pending")
        order.status = "pending"
        order.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(order)
        return order

    def cancel_order(self, db: Session, order: AppCreditOrder) -> AppCreditOrder:
        """关闭一笔未支付的积分订单。"""
        _assert_credit_transition(order.status, "closed")
        order.status = "closed"
        order.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(order)
        return order

    def fulfill_order(
        self,
        db: Session,
        order: AppCreditOrder,
        provider_trade_no: Optional[str] = None,
    ) -> AppCreditOrder:
        """把订单标记为已支付并幂等发放所购积分。"""
        if order.status == "paid":
            return order
        _assert_credit_transition(order.status, "paid")
        now = datetime.utcnow()
        order.status = "paid"
        order.paid_at = now
        order.updated_at = now
        if provider_trade_no:
            order.provider_trade_no = provider_trade_no

        user = db.query(AppUser).get(order.user_id)
        if user is not None:
            grant_credit_purchase(
                db,
                user=user,
                amount=int(order.credit_amount or 0),
                order_no=order.order_no,
                package_code=order.package_code,
            )

        db.commit()
        db.refresh(order)
        logger.info("credit order fulfilled: %s user=%d credits=%d", order.order_no, order.user_id, order.credit_amount)
        return order

    def record_payment_event(
        self,
        db: Session,
        order_no: str,
        provider: str,
        event_type: str,
        provider_event_id: str,
        raw_payload: str,
        signature: Optional[str] = None,
        signature_valid: bool = False,
    ) -> AppCreditPaymentEvent:
        """按 provider_event_id 幂等地落库一条原始回调事件（重复回调直接返回旧记录）。"""
        existing = (
            db.query(AppCreditPaymentEvent)
            .filter(AppCreditPaymentEvent.provider_event_id == provider_event_id)
            .first()
        )
        if existing:
            return existing
        ev = AppCreditPaymentEvent(
            order_no=order_no,
            provider=provider,
            event_type=event_type,
            provider_event_id=provider_event_id,
            raw_payload=raw_payload,
            signature=signature,
            signature_valid=signature_valid,
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
        return ev

    def process_callback(
        self,
        db: Session,
        result: CallbackResult,
        signature_raw: Optional[str] = None,
    ) -> Optional[CreditCallbackOutcome]:
        """处理已验签的支付回调，并在校验通过时履约发放积分。

        校验顺序：订单存在 → 事件去重 → 签名有效 → 状态为 paid → 金额一致。
        任一步不满足都只记录事件并返回 reason，不抛异常，避免渠道反复重试。

        Args:
            db: 数据库会话。
            result: gateway 验签后的回调结果。
            signature_raw: 原始签名字串，留档便于事后排查。

        Returns:
            :class:`CreditCallbackOutcome`；订单号缺失或订单不存在时返回 None。
        """
        order_no = result.out_trade_no or ""
        if not order_no:
            return None
        order = self.get_order(db, order_no)
        if order is None:
            return None

        provider = result.provider or "unknown"
        event_id = result.event_id or f"{provider}-{datetime.utcnow().timestamp()}"
        event = self.record_payment_event(
            db=db,
            order_no=order_no,
            provider=provider,
            event_type=result.event_type or "callback.received",
            provider_event_id=event_id,
            raw_payload=result.raw_payload[:4096] if result.raw_payload else "",
            signature=(signature_raw or "")[:512] or None,
            signature_valid=bool(result.signature_valid),
        )

        if event.processed:
            return CreditCallbackOutcome(event=event, already_processed=True)
        if not result.signature_valid:
            return CreditCallbackOutcome(event=event, reason="signature_invalid")
        if result.status != "paid":
            return CreditCallbackOutcome(event=event, reason=f"status={result.status}")
        if order.amount_cents and result.amount_cents and order.amount_cents != result.amount_cents:
            return CreditCallbackOutcome(event=event, reason="amount_mismatch")
        if order.status == "paid":
            self._mark_event_processed(db, event)
            return CreditCallbackOutcome(event=event, already_processed=True)

        try:
            self.fulfill_order(db, order, provider_trade_no=result.provider_trade_no)
        except Exception as exc:  # noqa: BLE001
            logger.exception("credit fulfill from callback failed: %s", order.order_no)
            return CreditCallbackOutcome(event=event, reason=f"fulfill_failed:{exc}")

        self._mark_event_processed(db, event)
        return CreditCallbackOutcome(event=event, fulfilled=True)

    def _mark_event_processed(self, db: Session, event: AppCreditPaymentEvent) -> None:
        """把回调事件标记为已处理，避免渠道重复通知时重复履约。"""
        if event.processed:
            return
        event.processed = True
        event.processed_at = datetime.utcnow()
        db.add(event)
        db.commit()


__all__ = [
    "CreditCallbackOutcome",
    "CreditOrderService",
    "serialize_credit_order",
    "serialize_credit_package",
]
