# -*- coding: utf-8 -*-
"""Credit balance and referral rewards for To C users."""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from src.storage import AppCreditLedger, AppOrder, AppUser, AppUserReferral
from src.users.platform_settings import get_platform_setting_value


KIND_ANALYSIS = "analysis"
KIND_AGENT = "agent"

REASON_REGISTER_BONUS = "register_bonus"
REASON_REFERRAL_SIGNUP = "referral_signup"
REASON_SUBSCRIPTION_BONUS = "subscription_bonus"
REASON_REFERRAL_PAID = "referral_paid"
REASON_CONSUME = "consume"
REASON_REFUND = "refund"
REASON_ADMIN_ADJUST = "admin_adjust"

_CODE_ALPHABET = string.ascii_uppercase + string.digits


@dataclass(frozen=True)
class CreditSettings:
    enabled: bool
    registration_bonus: int
    referral_signup_bonus: int
    monthly_subscription_bonus: int
    yearly_subscription_bonus: int
    referral_paid_bonus: int
    analysis_cost: int
    agent_cost: int


@dataclass(frozen=True)
class CreditOutcome:
    user: AppUser
    kind: str
    cost: int
    balance: int
    consumed: bool
    enabled: bool
    exceeded: bool
    ledger_id: Optional[int] = None

    @property
    def remaining(self) -> int:
        return max(0, self.balance)


def load_credit_settings(db: Optional[Session]) -> CreditSettings:
    return CreditSettings(
        enabled=bool(get_platform_setting_value(db, "CREDIT_SYSTEM_ENABLED")),
        registration_bonus=int(get_platform_setting_value(db, "CREDIT_REGISTRATION_BONUS")),
        referral_signup_bonus=int(get_platform_setting_value(db, "CREDIT_REFERRAL_SIGNUP_BONUS")),
        monthly_subscription_bonus=int(get_platform_setting_value(db, "CREDIT_MONTHLY_SUBSCRIPTION_BONUS")),
        yearly_subscription_bonus=int(get_platform_setting_value(db, "CREDIT_YEARLY_SUBSCRIPTION_BONUS")),
        referral_paid_bonus=int(get_platform_setting_value(db, "CREDIT_REFERRAL_PAID_BONUS")),
        analysis_cost=int(get_platform_setting_value(db, "CREDIT_ANALYSIS_COST")),
        agent_cost=int(get_platform_setting_value(db, "CREDIT_AGENT_COST")),
    )


def _credit_cost_for(settings: CreditSettings, kind: str) -> int:
    if kind == KIND_ANALYSIS:
        return max(0, int(settings.analysis_cost))
    if kind == KIND_AGENT:
        return max(0, int(settings.agent_cost))
    raise ValueError(f"unsupported credit kind: {kind!r}")


def normalize_referral_code(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def ensure_referral_code(db: Session, user: AppUser) -> str:
    code = normalize_referral_code(getattr(user, "referral_code", None))
    if code:
        return code
    for _ in range(12):
        candidate = "U" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(9))
        exists = db.query(AppUser.id).filter(AppUser.referral_code == candidate).first()
        if exists is None:
            user.referral_code = candidate
            db.add(user)
            db.flush()
            return candidate
    raise RuntimeError("failed to generate unique referral code")


def get_user_by_referral_code(db: Session, code: Optional[str]) -> Optional[AppUser]:
    normalized = normalize_referral_code(code)
    if not normalized:
        return None
    return db.query(AppUser).filter(AppUser.referral_code == normalized).first()


def get_referral_for_invitee(db: Session, invitee_user_id: int) -> Optional[AppUserReferral]:
    return (
        db.query(AppUserReferral)
        .filter(AppUserReferral.invitee_user_id == int(invitee_user_id))
        .first()
    )


def get_referred_users_count(db: Session, inviter_user_id: int) -> int:
    return (
        db.query(AppUserReferral)
        .filter(AppUserReferral.inviter_user_id == int(inviter_user_id))
        .count()
    )


def _existing_ledger_by_key(db: Session, idempotency_key: Optional[str]) -> Optional[AppCreditLedger]:
    if not idempotency_key:
        return None
    return (
        db.query(AppCreditLedger)
        .filter(AppCreditLedger.idempotency_key == idempotency_key)
        .first()
    )


def add_credits(
    db: Session,
    *,
    user: AppUser,
    amount: int,
    reason: str,
    related_type: Optional[str] = None,
    related_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    note: Optional[str] = None,
) -> Optional[AppCreditLedger]:
    amount = int(amount or 0)
    if amount <= 0:
        return None

    existing = _existing_ledger_by_key(db, idempotency_key)
    if existing is not None:
        return existing

    current = int(getattr(user, "credit_balance", 0) or 0)
    next_balance = current + amount
    user.credit_balance = next_balance
    db.add(user)
    row = AppCreditLedger(
        user_id=user.id,
        delta=amount,
        balance_after=next_balance,
        reason=reason,
        related_type=related_type,
        related_id=related_id,
        idempotency_key=idempotency_key,
        note=(note or "")[:255] or None,
    )
    db.add(row)
    db.flush()
    return row


def consume_credits(
    db: Session,
    *,
    user: AppUser,
    amount: int,
    kind: str,
    reason: str = REASON_CONSUME,
    related_type: Optional[str] = None,
    related_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    note: Optional[str] = None,
) -> Optional[AppCreditLedger]:
    amount = int(amount or 0)
    if amount <= 0:
        return None

    existing = _existing_ledger_by_key(db, idempotency_key)
    if existing is not None:
        return existing

    current = int(getattr(user, "credit_balance", 0) or 0)
    if current < amount:
        raise ValueError("credit balance is insufficient")

    next_balance = current - amount
    user.credit_balance = next_balance
    db.add(user)
    row = AppCreditLedger(
        user_id=user.id,
        delta=-amount,
        balance_after=next_balance,
        reason=reason,
        related_type=related_type or kind,
        related_id=related_id,
        idempotency_key=idempotency_key,
        note=(note or f"consume:{kind}")[:255],
    )
    db.add(row)
    db.flush()
    return row


def refund_credits(
    db: Session,
    *,
    user: AppUser,
    amount: int,
    kind: str,
    related_type: Optional[str] = None,
    related_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    note: Optional[str] = None,
) -> Optional[AppCreditLedger]:
    return add_credits(
        db,
        user=user,
        amount=int(amount or 0),
        reason=REASON_REFUND,
        related_type=related_type or kind,
        related_id=related_id,
        idempotency_key=idempotency_key,
        note=note or f"refund:{kind}",
    )


def register_referral(
    db: Session,
    *,
    inviter: Optional[AppUser],
    invitee: AppUser,
    invite_code: Optional[str],
    settings: Optional[CreditSettings] = None,
) -> Optional[AppUserReferral]:
    if inviter is None or invitee is None:
        return None
    if int(inviter.id) == int(invitee.id):
        return None

    existing = get_referral_for_invitee(db, int(invitee.id))
    if existing is not None:
        return existing

    row = AppUserReferral(
        inviter_user_id=int(inviter.id),
        invitee_user_id=int(invitee.id),
        invite_code=(invite_code or "")[:64] or None,
    )
    db.add(row)
    db.flush()

    settings = settings or load_credit_settings(db)
    if settings.enabled and settings.referral_signup_bonus > 0:
        ledger = add_credits(
            db,
            user=inviter,
            amount=settings.referral_signup_bonus,
            reason=REASON_REFERRAL_SIGNUP,
            related_type="user",
            related_id=str(invitee.id),
            idempotency_key=f"referral-signup:{invitee.id}",
            note=f"invitee:{invitee.email}",
        )
        if ledger is not None:
            row.signup_reward_credited_at = datetime.utcnow()
            db.add(row)
            db.flush()
    return row


def grant_registration_bonus(db: Session, *, user: AppUser, settings: Optional[CreditSettings] = None) -> None:
    settings = settings or load_credit_settings(db)
    if not settings.enabled or settings.registration_bonus <= 0:
        return
    add_credits(
        db,
        user=user,
        amount=settings.registration_bonus,
        reason=REASON_REGISTER_BONUS,
        related_type="user",
        related_id=str(user.id),
        idempotency_key=f"register-bonus:{user.id}",
    )


def grant_subscription_credit_rewards(
    db: Session,
    *,
    order: AppOrder,
    user: AppUser,
    settings: Optional[CreditSettings] = None,
) -> None:
    settings = settings or load_credit_settings(db)
    if not settings.enabled:
        return

    grant_days = int(getattr(order, "grant_days", 0) or 0)
    subscription_bonus = (
        settings.yearly_subscription_bonus
        if grant_days >= 365
        else settings.monthly_subscription_bonus
    )
    if subscription_bonus > 0:
        add_credits(
            db,
            user=user,
            amount=subscription_bonus,
            reason=REASON_SUBSCRIPTION_BONUS,
            related_type="order",
            related_id=order.order_no,
            idempotency_key=f"subscription-bonus:{order.order_no}",
            note=f"plan:{order.plan_code}",
        )

    referral = get_referral_for_invitee(db, int(user.id))
    if referral is None or referral.paid_reward_credited_at is not None:
        return
    inviter = db.query(AppUser).filter(AppUser.id == referral.inviter_user_id).first()
    if inviter is None or settings.referral_paid_bonus <= 0:
        return

    ledger = add_credits(
        db,
        user=inviter,
        amount=settings.referral_paid_bonus,
        reason=REASON_REFERRAL_PAID,
        related_type="order",
        related_id=order.order_no,
        idempotency_key=f"referral-paid:{order.order_no}",
        note=f"invitee:{user.email}",
    )
    if ledger is not None:
        now = datetime.utcnow()
        referral.first_paid_order_no = referral.first_paid_order_no or order.order_no
        referral.first_paid_at = referral.first_paid_at or now
        referral.paid_reward_credited_at = referral.paid_reward_credited_at or now
        db.add(referral)
        db.flush()


def enforce_credits(
    db: Session,
    *,
    user: AppUser,
    kind: str,
    related_type: Optional[str] = None,
    related_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> CreditOutcome:
    settings = load_credit_settings(db)
    cost = _credit_cost_for(settings, kind)
    balance = int(getattr(user, "credit_balance", 0) or 0)
    if not settings.enabled or cost <= 0:
        return CreditOutcome(
            user=user,
            kind=kind,
            cost=cost,
            balance=balance,
            consumed=False,
            enabled=settings.enabled,
            exceeded=False,
        )
    if balance < cost:
        return CreditOutcome(
            user=user,
            kind=kind,
            cost=cost,
            balance=balance,
            consumed=False,
            enabled=True,
            exceeded=True,
        )
    ledger = consume_credits(
        db,
        user=user,
        amount=cost,
        kind=kind,
        related_type=related_type or kind,
        related_id=related_id,
        idempotency_key=idempotency_key,
    )
    db.flush()
    return CreditOutcome(
        user=user,
        kind=kind,
        cost=cost,
        balance=int(getattr(user, "credit_balance", 0) or 0),
        consumed=ledger is not None,
        enabled=True,
        exceeded=False,
        ledger_id=int(ledger.id) if ledger is not None and ledger.id is not None else None,
    )


def refund_consumed_credits(
    db: Session,
    *,
    user: AppUser,
    outcome: Optional[CreditOutcome],
    related_type: Optional[str] = None,
    related_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    if outcome is None or not outcome.consumed or outcome.cost <= 0:
        return
    refund_credits(
        db,
        user=user,
        amount=outcome.cost,
        kind=outcome.kind,
        related_type=related_type or outcome.kind,
        related_id=related_id,
        idempotency_key=idempotency_key or f"credit-refund:{outcome.ledger_id}",
    )


def credit_exceeded_payload(outcome: CreditOutcome) -> dict:
    return {
        "error": "credit_exceeded",
        "message": f"积分不足，当前余额 {outcome.balance}，本次需要 {outcome.cost}",
        "kind": outcome.kind,
        "cost": outcome.cost,
        "balance": outcome.balance,
        "remaining": outcome.remaining,
    }


def serialize_credit_snapshot(db: Session, user: AppUser) -> dict:
    settings = load_credit_settings(db)
    ensure_referral_code(db, user)
    return {
        "enabled": settings.enabled,
        "balance": int(getattr(user, "credit_balance", 0) or 0),
        "referralCode": user.referral_code,
        "referredUsers": get_referred_users_count(db, int(user.id)),
        "costs": {
            "analysis": max(0, settings.analysis_cost),
            "agent": max(0, settings.agent_cost),
        },
        "rewards": {
            "registration": max(0, settings.registration_bonus),
            "referralSignup": max(0, settings.referral_signup_bonus),
            "monthlySubscription": max(0, settings.monthly_subscription_bonus),
            "yearlySubscription": max(0, settings.yearly_subscription_bonus),
            "referralPaid": max(0, settings.referral_paid_bonus),
        },
    }


__all__ = [
    "CreditOutcome",
    "CreditSettings",
    "KIND_AGENT",
    "KIND_ANALYSIS",
    "REASON_ADMIN_ADJUST",
    "REASON_CONSUME",
    "REASON_REFERRAL_PAID",
    "REASON_REFERRAL_SIGNUP",
    "REASON_REFUND",
    "REASON_REGISTER_BONUS",
    "REASON_SUBSCRIPTION_BONUS",
    "add_credits",
    "consume_credits",
    "credit_exceeded_payload",
    "enforce_credits",
    "ensure_referral_code",
    "get_user_by_referral_code",
    "grant_registration_bonus",
    "grant_subscription_credit_rewards",
    "load_credit_settings",
    "normalize_referral_code",
    "refund_consumed_credits",
    "refund_credits",
    "register_referral",
    "serialize_credit_snapshot",
]
