# -*- coding: utf-8 -*-
"""订单服务层 (Phase 5)。

职责：
- 订单创建、查询、状态机流转
- 退款申请、发票申请
- 支付回调驱动（幂等）
- 支付成功后 grant_plan 开通订阅

状态机（不可回退）：
  created ──超时/取消──> closed
     │
     └──发起支付──> pending ──通道回调成功──> paid ──申请退款──> refunded / partial_refunded
                        │
                        └──通道回调失败──> failed
"""

from __future__ import annotations

import json
import logging
import random
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from src.storage import AppPaymentEvent as _AppPaymentEvent  # noqa: F401

from src.storage import (
    AppInvoice,
    AppOrder,
    AppPaymentEvent,
    AppPlan,
    AppRefund,
    AppSubscription,
    AppUser,
)
from src.users.plans import resolve_user_plan, grant_plan
from src.services.billing.gateways import CallbackResult, get_gateway

logger = logging.getLogger(__name__)

# ── 有效状态机转换白名单 ────────────────────────────────────────────────────
_VALID_TRANSITIONS: set[tuple[str, str]] = {
    ("created", "pending"),
    ("created", "closed"),
    ("pending", "paid"),
    ("pending", "failed"),
    ("pending", "closed"),
    ("paid", "refunded"),
    ("paid", "partial_refunded"),
}

# ── 业务常量 ─────────────────────────────────────────────────────────────────
ORDER_EXPIRE_MINUTES = 15  # 默认订单超时时间（分钟）


# ── 编号生成 ──────────────────────────────────────────────────────────────────

def _gen_no(prefix: str) -> str:
    today = datetime.now().strftime("%Y%m%d")
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    return f"{prefix}{today}{suffix}"


def gen_order_no() -> str:
    return _gen_no("DSA")


def gen_refund_no() -> str:
    return _gen_no("RF")


def gen_invoice_no() -> str:
    return _gen_no("INV")


# ── 状态机校验 ────────────────────────────────────────────────────────────────

class InvalidTransitionError(ValueError):
    pass


def _assert_transition(current: str, new: str) -> None:
    if (current, new) not in _VALID_TRANSITIONS:
        raise InvalidTransitionError(
            f"订单状态不允许从 '{current}' 变更为 '{new}'"
        )


# ── 序列化 ────────────────────────────────────────────────────────────────────

def serialize_order(order: AppOrder) -> dict:
    return {
        "orderNo": order.order_no,
        "planCode": order.plan_code,
        "grantDays": order.grant_days,
        "amountCents": order.amount_cents,
        "originalAmountCents": order.original_amount_cents,
        "discountCents": order.discount_cents,
        "couponCode": order.coupon_code,
        "currency": order.currency,
        "provider": order.provider,
        "status": order.status,
        "paidAt": order.paid_at.isoformat() if order.paid_at else None,
        "expiresAt": order.expires_at.isoformat() if order.expires_at else None,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
        "updatedAt": order.updated_at.isoformat() if order.updated_at else None,
    }


def serialize_refund(refund: AppRefund) -> dict:
    return {
        "refundNo": refund.refund_no,
        "orderNo": refund.order_no,
        "amountCents": refund.amount_cents,
        "reason": refund.reason,
        "status": refund.status,
        "createdAt": refund.created_at.isoformat() if refund.created_at else None,
        "approvedAt": refund.approved_at.isoformat() if refund.approved_at else None,
        "refundedAt": refund.refunded_at.isoformat() if refund.refunded_at else None,
    }


@dataclass
class CallbackOutcome:
    """:meth:`OrderService.process_callback` 的结构化结果。

    Attributes:
        event: 落库的 :class:`AppPaymentEvent` (幂等读取或新建)。
        fulfilled: 本次调用是否触发了 ``fulfill_order``。
        already_processed: True 表示该 event 之前已被处理过 (幂等去重)。
        reason: 未驱动业务的原因 (signature_invalid / amount_mismatch /
            order_not_found / status=xxx / fulfill_failed:...), 成功时为 None。
    """

    event: AppPaymentEvent
    fulfilled: bool = False
    already_processed: bool = False
    reason: Optional[str] = None


def serialize_invoice(invoice: AppInvoice) -> dict:
    return {
        "invoiceNo": invoice.invoice_no,
        "orderNo": invoice.order_no,
        "invoiceType": invoice.invoice_type,
        "title": invoice.title,
        "taxId": invoice.tax_id,
        "amountCents": invoice.amount_cents,
        "email": invoice.email,
        "status": invoice.status,
        "issuedUrl": invoice.issued_url,
        "createdAt": invoice.created_at.isoformat() if invoice.created_at else None,
        "issuedAt": invoice.issued_at.isoformat() if invoice.issued_at else None,
    }


# ── OrderService ──────────────────────────────────────────────────────────────

class OrderService:
    """订单全生命周期管理。"""

    def create_order(
        self,
        db: Session,
        user: AppUser,
        plan_code: str,
        provider: str,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        coupon_code: Optional[str] = None,
        expire_minutes: int = ORDER_EXPIRE_MINUTES,
    ) -> AppOrder:
        """创建新订单，返回 AppOrder 实例。

        金额从 ``app_plans`` 表读取并快照到 ``quote_snapshot``；
        同一用户同一套餐 5 分钟内已有未支付订单时直接返回已有订单（幂等）。
        """
        now = datetime.utcnow()

        existing = (
            db.query(AppOrder)
            .filter(
                AppOrder.user_id == user.id,
                AppOrder.plan_code == plan_code,
                AppOrder.status.in_(["created", "pending"]),
                AppOrder.expires_at > now,
            )
            .order_by(AppOrder.created_at.desc())
            .first()
        )
        if existing:
            return existing

        plan_row = db.query(AppPlan).filter(AppPlan.code == plan_code, AppPlan.is_active.is_(True)).first()
        if plan_row is None:
            raise ValueError(f"套餐 '{plan_code}' 不存在或已下架")

        amount = plan_row.price_cents
        discount = 0
        if coupon_code:
            pass

        quote = {
            "planCode": plan_row.code,
            "planName": plan_row.name,
            "priceCents": plan_row.price_cents,
            "currency": plan_row.currency,
            "capturedAt": now.isoformat(),
        }

        order = AppOrder(
            order_no=gen_order_no(),
            user_id=user.id,
            plan_code=plan_code,
            grant_days=30 if "yearly" not in plan_code else 365,
            amount_cents=max(0, amount - discount),
            original_amount_cents=amount,
            discount_cents=discount,
            coupon_code=coupon_code,
            currency=plan_row.currency or "CNY",
            provider=provider,
            status="created",
            client_ip=client_ip,
            user_agent=user_agent,
            quote_snapshot=json.dumps(quote, ensure_ascii=False),
            expires_at=now + timedelta(minutes=expire_minutes),
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        logger.info("order created: %s user=%d plan=%s amount=%d", order.order_no, user.id, plan_code, order.amount_cents)
        return order

    def get_order(self, db: Session, order_no: str, user_id: Optional[int] = None) -> Optional[AppOrder]:
        q = db.query(AppOrder).filter(AppOrder.order_no == order_no)
        if user_id is not None:
            q = q.filter(AppOrder.user_id == user_id)
        return q.first()

    def list_orders(self, db: Session, user_id: int, limit: int = 50) -> List[AppOrder]:
        return (
            db.query(AppOrder)
            .filter(AppOrder.user_id == user_id)
            .order_by(AppOrder.created_at.desc())
            .limit(limit)
            .all()
        )

    def cancel_order(self, db: Session, order: AppOrder) -> AppOrder:
        _assert_transition(order.status, "closed")
        order.status = "closed"
        order.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(order)
        return order

    def mark_pending(self, db: Session, order: AppOrder) -> AppOrder:
        _assert_transition(order.status, "pending")
        order.status = "pending"
        order.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(order)
        return order

    def fulfill_order(
        self,
        db: Session,
        order: AppOrder,
        provider_trade_no: Optional[str] = None,
        settings=None,
    ) -> AppOrder:
        """标记订单已支付并开通套餐。幂等：已是 paid 时直接返回。"""
        if order.status == "paid":
            return order
        _assert_transition(order.status, "paid")
        now = datetime.utcnow()
        order.status = "paid"
        order.paid_at = now
        order.updated_at = now
        if provider_trade_no:
            order.provider_trade_no = provider_trade_no

        user = db.query(AppUser).get(order.user_id)
        if user is not None:
            grant_plan(
                db,
                user,
                plan_code=order.plan_code,
                grant_days=order.grant_days,
                source="paid",
                note=f"order:{order.order_no}",
            )

        db.commit()
        db.refresh(order)
        logger.info("order fulfilled: %s user=%d plan=%s", order.order_no, order.user_id, order.plan_code)
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
    ) -> AppPaymentEvent:
        existing = db.query(AppPaymentEvent).filter(AppPaymentEvent.provider_event_id == provider_event_id).first()
        if existing:
            return existing

        ev = AppPaymentEvent(
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

    # ── 通道回调处理 ─────────────────────────────────────────────────────────

    def process_callback(
        self,
        db: Session,
        result: CallbackResult,
        signature_raw: Optional[str] = None,
    ) -> "CallbackOutcome":
        """根据 :class:`CallbackResult` 把回调驱动到业务层 (幂等)。

        步骤:
        1. 落 ``app_payment_events`` (signature_valid=False 也落, 仅审计);
        2. 已处理过的事件直接返回 ``already_processed=True`` (幂等);
        3. ``signature_valid=False`` 直接返回, 不触发业务;
        4. 业务驱动:
           - ``status=='paid'``: 校验金额一致 → ``fulfill_order``。
             金额不一致时仅落库 + 拒绝驱动 (留待对账脚本告警)。
           - 其他状态目前仅落库, 不驱动 (refund 状态由 admin 主动审批)。
        5. 驱动成功后把 event.processed=True 持久化 (幂等键)。

        Returns:
            :class:`CallbackOutcome`, 调用方可据此构造 HTTP 响应。
        """
        provider = result.provider or "unknown"
        out_trade_no = result.out_trade_no or "unknown"
        event_id = result.event_id or f"{provider}-{datetime.utcnow().timestamp()}"

        event = self.record_payment_event(
            db=db,
            order_no=out_trade_no,
            provider=provider,
            event_type=result.event_type or "callback.received",
            provider_event_id=event_id,
            raw_payload=result.raw_payload[:4096] if result.raw_payload else "",
            signature=(signature_raw or "")[:512] or None,
            signature_valid=bool(result.signature_valid),
        )

        if event.processed:
            return CallbackOutcome(event=event, fulfilled=False, already_processed=True)

        if not result.signature_valid:
            return CallbackOutcome(event=event, fulfilled=False, reason="signature_invalid")

        # 仅 paid 事件触发 fulfill; 其它状态留给运营 / 对账
        if result.status != "paid" or not result.out_trade_no:
            return CallbackOutcome(event=event, fulfilled=False, reason=f"status={result.status}")

        order = self.get_order(db, result.out_trade_no)
        if order is None:
            logger.warning("callback for missing order: %s provider=%s", result.out_trade_no, provider)
            return CallbackOutcome(event=event, fulfilled=False, reason="order_not_found")

        if order.amount_cents and result.amount_cents and order.amount_cents != result.amount_cents:
            logger.error(
                "callback amount mismatch order=%s local=%d channel=%d",
                order.order_no,
                order.amount_cents,
                result.amount_cents,
            )
            return CallbackOutcome(event=event, fulfilled=False, reason="amount_mismatch")

        if order.status == "paid":
            # 已 fulfill 过, 标记 processed 即可
            self._mark_event_processed(db, event)
            return CallbackOutcome(event=event, fulfilled=False, already_processed=True)

        try:
            self.fulfill_order(db, order, provider_trade_no=result.provider_trade_no)
        except Exception as exc:  # noqa: BLE001
            logger.exception("fulfill from callback failed: %s", order.order_no)
            return CallbackOutcome(event=event, fulfilled=False, reason=f"fulfill_failed:{exc}")

        self._mark_event_processed(db, event)
        return CallbackOutcome(event=event, fulfilled=True)

    def _mark_event_processed(self, db: Session, event: AppPaymentEvent) -> None:
        if event.processed:
            return
        event.processed = True
        event.processed_at = datetime.utcnow()
        db.add(event)
        db.commit()

    # ── 退款 ────────────────────────────────────────────────────────────────

    def create_refund(
        self,
        db: Session,
        order: AppOrder,
        user: AppUser,
        amount_cents: int,
        reason: str,
    ) -> AppRefund:
        if order.status not in ("paid",):
            raise ValueError("只有已支付的订单才可申请退款")

        existing = db.query(AppRefund).filter(
            AppRefund.order_no == order.order_no,
            AppRefund.status.in_(["pending", "approved"]),
        ).first()
        if existing:
            raise ValueError("该订单已有进行中的退款申请")

        refund = AppRefund(
            refund_no=gen_refund_no(),
            order_no=order.order_no,
            user_id=user.id,
            amount_cents=amount_cents,
            reason=reason,
            status="pending",
            revoke_subscription=True,
        )
        db.add(refund)
        db.commit()
        db.refresh(refund)
        return refund

    def get_refund(self, db: Session, refund_no: str, user_id: Optional[int] = None) -> Optional[AppRefund]:
        q = db.query(AppRefund).filter(AppRefund.refund_no == refund_no)
        if user_id is not None:
            q = q.filter(AppRefund.user_id == user_id)
        return q.first()

    def list_refunds(self, db: Session, user_id: int) -> List[AppRefund]:
        return (
            db.query(AppRefund)
            .filter(AppRefund.user_id == user_id)
            .order_by(AppRefund.created_at.desc())
            .all()
        )

    def list_refunds_admin(
        self, db: Session, status: Optional[str] = None, limit: int = 200
    ) -> List[AppRefund]:
        """运营后台用: 查询全平台退款记录, 可按 status 过滤。"""
        q = db.query(AppRefund)
        if status:
            q = q.filter(AppRefund.status == status)
        return q.order_by(AppRefund.created_at.desc()).limit(limit).all()

    def approve_refund(
        self,
        db: Session,
        refund: AppRefund,
        reviewer: AppUser,
        provider_refund_no: Optional[str] = None,
    ) -> AppRefund:
        """运营审核通过退款。

        - 标记 ``app_refunds`` 为 ``refunded``
        - 同步把对应 ``app_orders.status`` 流转为 ``refunded`` (或 partial)
        - ``revoke_subscription=True`` 时, 把 ``app_users.plan_expires_at`` 回退当前时间
          (相当于立即降级到 free, 简化退款后权益处理)

        通道调用:
        - 优先使用调用方显式传入的 ``provider_refund_no`` (人工补单 / 客服沙箱)。
        - 否则尝试从 :func:`get_gateway` 拿到对应 provider 的 gateway, 调用
          :meth:`PaymentGateway.refund` 取真实通道退款单号。Gateway 未配置或
          抛 :class:`NotImplementedError` 时回退为人工模式 (留 ``provider_refund_no``
          为空, 由后续运营登记)。
        """
        if refund.status not in ("pending",):
            raise ValueError(f"退款状态 '{refund.status}' 不允许 approve")
        now = datetime.utcnow()
        refund.status = "refunded"
        refund.reviewer_id = reviewer.id
        refund.approved_at = now
        refund.refunded_at = now

        order = (
            db.query(AppOrder).filter(AppOrder.order_no == refund.order_no).first()
        )

        # 通道退款调用 (仅在调用方未显式传入 provider_refund_no 时尝试)
        if not provider_refund_no and order is not None:
            provider_refund_no = self._invoke_gateway_refund(refund, order)

        if provider_refund_no:
            refund.provider_refund_no = provider_refund_no
        if order is not None and order.status == "paid":
            # 简化: full refund 路径; 后续接通道按金额比较决定 partial
            try:
                _assert_transition("paid", "refunded")
                order.status = "refunded"
                order.updated_at = now
                db.add(order)
            except InvalidTransitionError:
                logger.warning("订单状态机不允许 paid -> refunded: %s", order.order_no)

        # 立即降级用户 (revoke_subscription=True 时)
        if refund.revoke_subscription:
            user = db.query(AppUser).filter(AppUser.id == refund.user_id).first()
            if user is not None and user.plan_code and user.plan_code != "free":
                user.plan_expires_at = now
                user.plan_code = "free"
                db.add(user)

        db.add(refund)
        db.commit()
        db.refresh(refund)
        logger.info("refund approved: %s reviewer=%d", refund.refund_no, reviewer.id)
        return refund

    def reject_refund(
        self,
        db: Session,
        refund: AppRefund,
        reviewer: AppUser,
        note: Optional[str] = None,
    ) -> AppRefund:
        if refund.status not in ("pending",):
            raise ValueError(f"退款状态 '{refund.status}' 不允许 reject")
        refund.status = "rejected"
        refund.reviewer_id = reviewer.id
        refund.approved_at = datetime.utcnow()
        if note:
            base = refund.reason or ""
            refund.reason = (base + f"\n[reject] {note}")[:512]
        db.add(refund)
        db.commit()
        db.refresh(refund)
        logger.info("refund rejected: %s reviewer=%d", refund.refund_no, reviewer.id)
        return refund

    # ── 发票 ────────────────────────────────────────────────────────────────

    def create_invoice(
        self,
        db: Session,
        order: AppOrder,
        user: AppUser,
        invoice_type: str,
        title: str,
        email: str,
        tax_id: Optional[str] = None,
    ) -> AppInvoice:
        if order.status != "paid":
            raise ValueError("只有已支付的订单才可申请发票")
        if order.user_id != user.id:
            raise ValueError("订单不属于当前用户")

        existing = db.query(AppInvoice).filter(
            AppInvoice.order_no == order.order_no,
            AppInvoice.status.in_(["pending", "issued"]),
        ).first()
        if existing:
            raise ValueError("该订单已有发票申请")

        inv = AppInvoice(
            invoice_no=gen_invoice_no(),
            user_id=user.id,
            order_no=order.order_no,
            invoice_type=invoice_type,
            title=title,
            tax_id=tax_id,
            amount_cents=order.amount_cents,
            email=email,
            status="pending",
        )
        db.add(inv)
        db.commit()
        db.refresh(inv)
        return inv

    def get_invoice(self, db: Session, invoice_no: str, user_id: Optional[int] = None) -> Optional[AppInvoice]:
        q = db.query(AppInvoice).filter(AppInvoice.invoice_no == invoice_no)
        if user_id is not None:
            q = q.filter(AppInvoice.user_id == user_id)
        return q.first()

    def list_invoices(self, db: Session, user_id: int) -> List[AppInvoice]:
        return (
            db.query(AppInvoice)
            .filter(AppInvoice.user_id == user_id)
            .order_by(AppInvoice.created_at.desc())
            .all()
        )

    def list_invoices_admin(
        self, db: Session, status: Optional[str] = None, limit: int = 200
    ) -> List[AppInvoice]:
        q = db.query(AppInvoice)
        if status:
            q = q.filter(AppInvoice.status == status)
        return q.order_by(AppInvoice.created_at.desc()).limit(limit).all()

    def issue_invoice(
        self,
        db: Session,
        invoice: AppInvoice,
        reviewer: AppUser,
        issued_url: Optional[str] = None,
    ) -> AppInvoice:
        if invoice.status != "pending":
            raise ValueError(f"发票状态 '{invoice.status}' 不允许 issue")
        invoice.status = "issued"
        invoice.reviewer_id = reviewer.id
        invoice.issued_at = datetime.utcnow()
        if issued_url:
            invoice.issued_url = issued_url[:1024]
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        logger.info("invoice issued: %s reviewer=%d", invoice.invoice_no, reviewer.id)

        # 邮件回执 (失败仅日志, 不影响审核结果)
        try:
            _send_invoice_receipt(invoice)
        except Exception:  # noqa: BLE001
            logger.warning(
                "invoice receipt email failed: %s", invoice.invoice_no, exc_info=True
            )
        return invoice

    def reject_invoice(
        self,
        db: Session,
        invoice: AppInvoice,
        reviewer: AppUser,
    ) -> AppInvoice:
        if invoice.status != "pending":
            raise ValueError(f"发票状态 '{invoice.status}' 不允许 reject")
        invoice.status = "rejected"
        invoice.reviewer_id = reviewer.id
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        logger.info("invoice rejected: %s reviewer=%d", invoice.invoice_no, reviewer.id)
        return invoice

    # ── gateway 退款调用 ───────────────────────────────────────────────────

    def _invoke_gateway_refund(self, refund: AppRefund, order: AppOrder) -> Optional[str]:
        """尝试通过通道 gateway 发起退款, 失败时回退到人工模式。

        Returns:
            通道返回的退款单号 (``provider_refund_no``); 失败 / 未配置时返回 ``None``,
            由调用方决定是否后续登记。
        """
        provider = (order.provider or "").lower()
        if provider not in ("wechat", "alipay"):
            return None

        try:
            gateway = get_gateway(provider)
        except Exception:  # noqa: BLE001
            logger.warning("get_gateway(%s) raised", provider, exc_info=True)
            return None
        if gateway is None:
            logger.info(
                "gateway not configured for refund: provider=%s refund=%s",
                provider, refund.refund_no,
            )
            return None

        try:
            return gateway.refund(
                out_trade_no=order.order_no,
                out_refund_no=refund.refund_no,
                amount_cents=refund.amount_cents,
                total_cents=order.amount_cents,
                reason=refund.reason,
            )
        except NotImplementedError:
            # SDK 未接入: 留待 W7 SDK 切片; 当前回落人工补单
            logger.info(
                "gateway.refund not implemented (provider=%s); manual fallback",
                provider,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            # 真实通道退款失败 — 不阻断审核状态 (避免卡住运营), 但记录原因供后续重试
            logger.error(
                "gateway.refund failed provider=%s refund=%s: %s",
                provider, refund.refund_no, exc,
            )
            return None

    # ── 订单 admin 查询 ─────────────────────────────────────────────────────

    def list_orders_admin(
        self,
        db: Session,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        provider: Optional[str] = None,
        limit: int = 200,
    ) -> List[AppOrder]:
        q = db.query(AppOrder)
        if status:
            q = q.filter(AppOrder.status == status)
        if user_id is not None:
            q = q.filter(AppOrder.user_id == int(user_id))
        if provider:
            q = q.filter(AppOrder.provider == provider)
        return q.order_by(AppOrder.created_at.desc()).limit(limit).all()


# ── 发票邮件回执 ──────────────────────────────────────────────────────────────

def _send_invoice_receipt(invoice: AppInvoice) -> None:
    """向用户邮箱发送发票开具完成的回执邮件。

    复用 :func:`src.users.email.get_email_backend`, 邮件后端按 ``USER_EMAIL_BACKEND``
    环境变量解析: 未配 SMTP 时退化为 :class:`LoggingEmailBackend`, 不会真正发件,
    便于本地 / CI 跑测试。
    """
    if not invoice or not invoice.email:
        return

    from src.users.email import EmailMessageDTO, get_email_backend

    yuan = (invoice.amount_cents or 0) / 100.0
    title = invoice.title or "(未填写)"
    invoice_no = invoice.invoice_no or ""
    order_no = invoice.order_no or ""
    download_line = (
        f"下载链接: {invoice.issued_url}"
        if invoice.issued_url
        else "下载链接将由客服在 24h 内补发,如有疑问请回复此邮件。"
    )

    body_text = (
        f"您好,\n\n"
        f"您的发票申请已开具完成。\n\n"
        f"  发票号  : {invoice_no}\n"
        f"  订单号  : {order_no}\n"
        f"  发票抬头: {title}\n"
        f"  开票金额: ¥{yuan:.2f} (CNY)\n"
        f"  发票类型: {('企业' if invoice.invoice_type == 'company' else '个人')}\n\n"
        f"{download_line}\n\n"
        f"如有任何问题, 请直接回复此邮件联系客服。\n"
        f"——\n"
        f"本邮件由系统自动发送。\n"
    )

    body_html = (
        "<html><body style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;\">"
        "<p>您好,</p>"
        "<p>您的发票申请已开具完成。</p>"
        "<table style=\"border-collapse:collapse;border:1px solid #e0e0e0;\">"
        f"<tr><td style=\"padding:6px 12px;\">发票号</td><td style=\"padding:6px 12px;\"><code>{invoice_no}</code></td></tr>"
        f"<tr><td style=\"padding:6px 12px;\">订单号</td><td style=\"padding:6px 12px;\"><code>{order_no}</code></td></tr>"
        f"<tr><td style=\"padding:6px 12px;\">发票抬头</td><td style=\"padding:6px 12px;\">{title}</td></tr>"
        f"<tr><td style=\"padding:6px 12px;\">开票金额</td><td style=\"padding:6px 12px;\">¥{yuan:.2f} (CNY)</td></tr>"
        f"<tr><td style=\"padding:6px 12px;\">发票类型</td><td style=\"padding:6px 12px;\">{'企业' if invoice.invoice_type == 'company' else '个人'}</td></tr>"
        "</table>"
        + (
            f"<p>下载链接: <a href=\"{invoice.issued_url}\">{invoice.issued_url}</a></p>"
            if invoice.issued_url
            else "<p>下载链接将由客服在 24h 内补发,如有疑问请联系客服。</p>"
        )
        + "<p style=\"color:#888;font-size:12px;\">本邮件由系统自动发送。</p>"
        "</body></html>"
    )

    backend = get_email_backend()
    backend.send(EmailMessageDTO(
        to=invoice.email,
        subject=f"[DSA] 发票已开具 - {invoice_no}",
        body_text=body_text,
        body_html=body_html,
    ))
    logger.info("invoice receipt sent: %s to=%s", invoice_no, invoice.email)
