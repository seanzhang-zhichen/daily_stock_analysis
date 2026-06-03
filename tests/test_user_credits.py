# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import AppOrder, AppPlatformSetting, AppPlan, AppUser, Base
from src.users.credits import (
    credit_exceeded_payload,
    enforce_credits,
    get_referral_for_invitee,
    grant_subscription_credit_rewards,
    register_referral,
    serialize_credit_snapshot,
)


def _user(email: str, *, balance: int = 0, referral_code: str | None = None) -> AppUser:
    return AppUser(
        email=email,
        password_hash="hash",
        status="active",
        plan_code="free",
        credit_balance=balance,
        referral_code=referral_code,
    )


class TestUserCredits(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.db.add(
            AppPlan(
                code="free",
                name="Free",
                daily_analysis_limit=5,
                daily_agent_limit=5,
                max_stocks=3,
                price_cents=0,
                currency="CNY",
                is_active=True,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def _settings(self, **values) -> None:
        defaults = {
            "CREDIT_SYSTEM_ENABLED": "true",
            "CREDIT_REFERRAL_SIGNUP_BONUS": "10",
            "CREDIT_MONTHLY_SUBSCRIPTION_BONUS": "30",
            "CREDIT_YEARLY_SUBSCRIPTION_BONUS": "365",
            "CREDIT_REFERRAL_PAID_BONUS": "80",
            "CREDIT_ANALYSIS_COST": "2",
            "CREDIT_AGENT_COST": "3",
        }
        defaults.update({key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in values.items()})
        for key, value in defaults.items():
            self.db.add(AppPlatformSetting(key=key, value=value))
        self.db.commit()

    def test_referral_signup_grants_inviter_bonus_once(self):
        self._settings()
        inviter = _user("inviter@example.com", referral_code="INVITER1")
        invitee = _user("invitee@example.com", referral_code="INVITEE1")
        self.db.add_all([inviter, invitee])
        self.db.commit()

        register_referral(self.db, inviter=inviter, invitee=invitee, invite_code="INVITER1")
        register_referral(self.db, inviter=inviter, invitee=invitee, invite_code="INVITER1")
        self.db.commit()

        self.db.refresh(inviter)
        referral = get_referral_for_invitee(self.db, invitee.id)
        self.assertEqual(inviter.credit_balance, 10)
        self.assertIsNotNone(referral)
        self.assertIsNotNone(referral.signup_reward_credited_at)

    def test_subscription_reward_and_referral_paid_reward_are_idempotent(self):
        self._settings()
        inviter = _user("inviter@example.com", referral_code="INVITER1")
        invitee = _user("invitee@example.com", referral_code="INVITEE1")
        self.db.add_all([inviter, invitee])
        self.db.flush()
        register_referral(self.db, inviter=inviter, invitee=invitee, invite_code="INVITER1")
        order = AppOrder(
            order_no="DSA20260603ABC",
            user_id=invitee.id,
            plan_code="pro",
            grant_days=30,
            amount_cents=3900,
            original_amount_cents=3900,
            discount_cents=0,
            currency="CNY",
            provider="manual",
            status="paid",
            paid_at=datetime.utcnow(),
        )
        self.db.add(order)
        self.db.commit()

        grant_subscription_credit_rewards(self.db, order=order, user=invitee)
        grant_subscription_credit_rewards(self.db, order=order, user=invitee)
        self.db.commit()

        self.db.refresh(inviter)
        self.db.refresh(invitee)
        self.assertEqual(invitee.credit_balance, 30)
        self.assertEqual(inviter.credit_balance, 90)  # 10 signup + 80 paid

    def test_enforce_credits_consumes_and_reports_insufficient_balance(self):
        self._settings(CREDIT_ANALYSIS_COST=5)
        user = _user("user@example.com", balance=4, referral_code="USERCODE1")
        self.db.add(user)
        self.db.commit()

        denied = enforce_credits(self.db, user=user, kind="analysis")
        self.assertTrue(denied.exceeded)
        self.assertEqual(credit_exceeded_payload(denied)["error"], "credit_exceeded")

        user.credit_balance = 6
        self.db.add(user)
        self.db.commit()
        allowed = enforce_credits(self.db, user=user, kind="analysis")
        self.db.commit()

        self.db.refresh(user)
        self.assertFalse(allowed.exceeded)
        self.assertTrue(allowed.consumed)
        self.assertEqual(user.credit_balance, 1)

    def test_credit_snapshot_generates_referral_code(self):
        self._settings(CREDIT_SYSTEM_ENABLED=False)
        user = _user("user@example.com", balance=7)
        self.db.add(user)
        self.db.commit()

        snapshot = serialize_credit_snapshot(self.db, user)
        self.db.commit()

        self.assertFalse(snapshot["enabled"])
        self.assertEqual(snapshot["balance"], 7)
        self.assertTrue(snapshot["referralCode"])


if __name__ == "__main__":
    unittest.main()
