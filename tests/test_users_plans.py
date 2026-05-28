# -*- coding: utf-8 -*-
"""Phase 2 plans/redeem 单元测试。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import (
    AppPlan,
    AppRedeemCode,
    AppSubscription,
    AppUser,
    Base,
)
from src.users.errors import UserError
from src.users.passwords import hash_password
from src.users.plans import (
    grant_plan,
    redeem_code,
    resolve_user_plan,
)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.user = AppUser(
            email="plans@example.com",
            password_hash=hash_password("pw12345678"),
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def seed_free_plan(
        self,
        *,
        daily_analysis_limit: int = 5,
        daily_agent_limit: int = 5,
        max_stocks: int = 3,
    ) -> AppPlan:
        plan = AppPlan(
            code="free",
            name="Free",
            daily_analysis_limit=daily_analysis_limit,
            daily_agent_limit=daily_agent_limit,
            max_stocks=max_stocks,
            can_webhook=False,
            price_cents=0,
        )
        self.db.add(plan)
        self.db.commit()
        return plan

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()


class TestPlansAndRedeem(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.seed_free_plan()
        # 建立 Pro 套餐定义
        self.pro_plan = AppPlan(
            code="pro",
            name="Pro",
            daily_analysis_limit=50,
            daily_agent_limit=50,
            max_stocks=30,
            can_webhook=True,
            price_cents=2900,
        )
        self.db.add(self.pro_plan)
        self.db.commit()

    def test_resolve_plan_free_by_default(self):
        plan = resolve_user_plan(self.db, self.user)
        self.assertEqual(plan.code, "free")
        self.assertEqual(plan.daily_analysis_limit, 5)
        self.assertEqual(plan.max_stocks, 3)

    def test_resolve_plan_free_uses_db_config_when_present(self):
        row = self.db.query(AppPlan).filter(AppPlan.code == "free").first()
        row.name = "Free Custom"
        row.daily_analysis_limit = 8
        row.daily_agent_limit = 6
        row.max_stocks = 4
        self.db.commit()
        plan = resolve_user_plan(self.db, self.user)
        self.assertEqual(plan.code, "free")
        self.assertEqual(plan.daily_analysis_limit, 8)
        self.assertEqual(plan.daily_agent_limit, 6)
        self.assertEqual(plan.max_stocks, 4)
        self.assertFalse(plan.can_webhook)

    def test_grant_plan_extends_expiry(self):
        sub = grant_plan(self.db, self.user, plan_code="pro", grant_days=30, source="manual")
        self.db.commit()
        self.assertIsInstance(sub, AppSubscription)
        self.assertEqual(self.user.plan_code, "pro")
        self.assertIsNotNone(self.user.plan_expires_at)

        plan = resolve_user_plan(self.db, self.user)
        self.assertEqual(plan.code, "pro")
        self.assertEqual(plan.daily_analysis_limit, 50)

    def test_grant_plan_appends_when_still_active(self):
        grant_plan(self.db, self.user, plan_code="pro", grant_days=10)
        self.db.commit()
        first_expiry = self.user.plan_expires_at
        grant_plan(self.db, self.user, plan_code="pro", grant_days=20)
        self.db.commit()
        # 续期应在原过期时间上累加
        self.assertGreater(self.user.plan_expires_at, first_expiry)

    def test_grant_plan_invalid_inputs(self):
        with self.assertRaises(UserError):
            grant_plan(self.db, self.user, plan_code="unknown_plan", grant_days=30)
        with self.assertRaises(UserError):
            grant_plan(self.db, self.user, plan_code="pro", grant_days=0)
        with self.assertRaises(UserError):
            grant_plan(self.db, self.user, plan_code="free", grant_days=30)

    def test_redeem_code_flow(self):
        code = AppRedeemCode(
            code="VIP-AAAA",
            plan_code="pro",
            grant_days=30,
        )
        self.db.add(code)
        self.db.commit()

        sub = redeem_code(self.db, self.user, code="VIP-AAAA")
        self.db.commit()
        self.assertEqual(sub.source, "redeem")
        self.assertEqual(self.user.plan_code, "pro")

        # 同一个 code 不能再用
        with self.assertRaises(UserError):
            redeem_code(self.db, self.user, code="VIP-AAAA")

    def test_redeem_code_expired(self):
        code = AppRedeemCode(
            code="OLD-XXX",
            plan_code="pro",
            grant_days=30,
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        self.db.add(code)
        self.db.commit()
        with self.assertRaises(UserError):
            redeem_code(self.db, self.user, code="OLD-XXX")

    def test_redeem_code_unknown(self):
        with self.assertRaises(UserError):
            redeem_code(self.db, self.user, code="NEVER-EXISTED")

    def test_expired_plan_falls_back_to_free(self):
        self.user.plan_code = "pro"
        self.user.plan_expires_at = datetime.utcnow() - timedelta(days=1)
        self.db.add(self.user)
        self.db.commit()
        plan = resolve_user_plan(self.db, self.user)
        self.assertEqual(plan.code, "free")

    def test_missing_free_plan_raises(self):
        self.db.query(AppPlan).filter(AppPlan.code == "free").delete()
        self.db.commit()
        with self.assertRaises(UserError):
            resolve_user_plan(self.db, self.user)


if __name__ == "__main__":
    unittest.main()
