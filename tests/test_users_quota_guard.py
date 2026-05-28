# -*- coding: utf-8 -*-
"""Phase 2 quota_guard 单元测试。

覆盖:
- 未登录调用方错误
- 登录用户首次扣减成功
- 上限耗尽返回 exceeded=True
- 不限额套餐仍记录用量
- refund_quota 正确回滚使用量
- quota_exceeded_payload 输出符合前端约定的字段
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import AppPlan, AppUser, Base
from src.users.passwords import hash_password
from src.users.quota import KIND_AGENT, KIND_ANALYSIS, get_used
from src.users.quota_guard import (
    QuotaOutcome,
    enforce_quota,
    quota_exceeded_payload,
    refund_quota,
)


class TestQuotaGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = SessionLocal()

        self.user = AppUser(
            email="qg@example.com",
            password_hash=hash_password("pw12345678"),
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.free_plan = AppPlan(
            code="free",
            name="Free",
            daily_analysis_limit=2,
            daily_agent_limit=2,
            max_stocks=3,
        )
        self.db.add(self.free_plan)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    # --- auth precondition -------------------------------------------------

    def test_no_user_raises(self):
        with self.assertRaises(ValueError):
            enforce_quota(self.db, user=None, kind=KIND_ANALYSIS)

    # --- consume / exceeded -----------------------------------------------

    def test_first_consume_returns_consumed(self):
        outcome = enforce_quota(
            self.db, user=self.user, kind=KIND_ANALYSIS
        )
        self.assertTrue(outcome.consumed)
        self.assertFalse(outcome.exceeded)
        self.assertFalse(outcome.bypassed)
        self.assertEqual(outcome.used, 1)
        self.assertEqual(outcome.limit, 2)
        self.assertEqual(outcome.remaining, 1)
        self.assertIsNotNone(outcome.plan)

    def test_exceeded_when_limit_hit(self):
        # 用满前 2 次
        first = enforce_quota(self.db, user=self.user, kind=KIND_ANALYSIS)
        second = enforce_quota(self.db, user=self.user, kind=KIND_ANALYSIS)
        self.assertTrue(first.consumed)
        self.assertTrue(second.consumed)
        self.assertEqual(second.remaining, 0)

        # 第 3 次应该 exceeded
        third = enforce_quota(self.db, user=self.user, kind=KIND_ANALYSIS)
        self.assertFalse(third.consumed)
        self.assertTrue(third.exceeded)
        self.assertEqual(third.used, 2)
        self.assertEqual(third.limit, 2)
        self.assertEqual(third.remaining, 0)

    def test_kinds_are_isolated(self):
        for _ in range(2):
            enforce_quota(self.db, user=self.user, kind=KIND_ANALYSIS)
        # analysis 已耗尽
        exceeded = enforce_quota(
            self.db, user=self.user, kind=KIND_ANALYSIS
        )
        self.assertTrue(exceeded.exceeded)
        # agent 仍可继续扣
        agent = enforce_quota(self.db, user=self.user, kind=KIND_AGENT)
        self.assertTrue(agent.consumed)

    # --- unlimited / pro plan ---------------------------------------------

    def test_pro_plan_uses_higher_limit(self):
        # 建一个 pro 套餐, 给 user 续期, 然后扣到原 free 上限以上仍可继续
        pro_plan = AppPlan(
            code="pro",
            name="Pro",
            daily_analysis_limit=5,
            daily_agent_limit=5,
            max_stocks=30,
        )
        self.db.add(pro_plan)
        self.user.plan_code = "pro"
        self.user.plan_expires_at = datetime.utcnow() + timedelta(days=30)
        self.db.add(self.user)
        self.db.commit()

        for _ in range(5):
            outcome = enforce_quota(
                self.db, user=self.user, kind=KIND_ANALYSIS
            )
            self.assertTrue(outcome.consumed)
            self.assertEqual(outcome.limit, 5)
        # 第 6 次应该 exceeded
        sixth = enforce_quota(self.db, user=self.user, kind=KIND_ANALYSIS)
        self.assertTrue(sixth.exceeded)

    def test_unlimited_plan_records_usage_but_never_exceeds(self):
        self.free_plan.daily_analysis_limit = 0
        self.db.add(self.free_plan)
        self.db.commit()
        for _ in range(3):
            outcome = enforce_quota(
                self.db, user=self.user, kind=KIND_ANALYSIS
            )
            self.assertTrue(outcome.consumed)
            self.assertFalse(outcome.exceeded)
            self.assertEqual(outcome.limit, 0)
        self.assertEqual(get_used(self.db, user_id=self.user.id, kind=KIND_ANALYSIS), 3)

    # --- refund -----------------------------------------------------------

    def test_refund_decreases_usage(self):
        outcome = enforce_quota(self.db, user=self.user, kind=KIND_ANALYSIS)
        self.assertTrue(outcome.consumed)
        self.assertEqual(get_used(self.db, user_id=self.user.id, kind=KIND_ANALYSIS), 1)
        refund_quota(self.db, user=self.user, kind=KIND_ANALYSIS, on_date=outcome.on_date)
        self.assertEqual(get_used(self.db, user_id=self.user.id, kind=KIND_ANALYSIS), 0)

    # --- payload formatting ----------------------------------------------

    def test_quota_exceeded_payload_shape(self):
        for _ in range(2):
            enforce_quota(self.db, user=self.user, kind=KIND_ANALYSIS)
        exceeded = enforce_quota(
            self.db, user=self.user, kind=KIND_ANALYSIS
        )
        payload = quota_exceeded_payload(exceeded)
        self.assertEqual(payload["error"], "quota_exceeded")
        self.assertEqual(payload["kind"], KIND_ANALYSIS)
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["used"], 2)
        self.assertEqual(payload["remaining"], 0)
        self.assertIn("planCode", payload)
        self.assertIn("planName", payload)
        self.assertIn("message", payload)


if __name__ == "__main__":
    unittest.main()
