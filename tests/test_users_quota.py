# -*- coding: utf-8 -*-
"""Phase 2 配额服务单元测试。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import AppUser, Base
from src.users.passwords import hash_password
from src.users.quota import (
    KIND_AGENT,
    KIND_ANALYSIS,
    KIND_NOTIFY,
    QuotaConfig,
    get_quota_snapshot,
    get_remaining,
    get_used,
    refund,
    try_consume,
)


class TestQuota(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.user = AppUser(
            email="quota@example.com",
            password_hash=hash_password("pw12345678"),
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_initial_state_is_empty(self):
        self.assertEqual(get_used(self.db, user_id=self.user.id, kind=KIND_ANALYSIS), 0)
        self.assertEqual(
            get_remaining(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=QuotaConfig(daily_limit=5)),
            5,
        )

    def test_try_consume_decrements_remaining(self):
        cfg = QuotaConfig(daily_limit=3)
        for expected_remaining in (2, 1, 0):
            ok = try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg)
            self.db.commit()
            self.assertTrue(ok)
            self.assertEqual(
                get_remaining(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg),
                expected_remaining,
            )

    def test_try_consume_returns_false_when_exhausted(self):
        cfg = QuotaConfig(daily_limit=1)
        self.assertTrue(try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg))
        self.db.commit()
        self.assertFalse(try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg))

    def test_unlimited_limit_keeps_counting_without_blocking(self):
        cfg = QuotaConfig(daily_limit=0)  # 0 / 负数 视作不限额
        for _ in range(10):
            self.assertTrue(try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg))
            self.db.commit()
        # 但仍记录了使用量, 便于运营查看
        self.assertEqual(get_used(self.db, user_id=self.user.id, kind=KIND_ANALYSIS), 10)
        self.assertIsNone(get_remaining(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg))

    def test_bypass_skips_consume(self):
        cfg = QuotaConfig(daily_limit=2)
        self.assertTrue(
            try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg, bypass=True)
        )
        self.db.commit()
        self.assertEqual(get_used(self.db, user_id=self.user.id, kind=KIND_ANALYSIS), 0)

    def test_refund_restores_count_but_not_below_zero(self):
        cfg = QuotaConfig(daily_limit=2)
        try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg)
        self.db.commit()
        self.assertEqual(get_used(self.db, user_id=self.user.id, kind=KIND_ANALYSIS), 1)
        refund(self.db, user_id=self.user.id, kind=KIND_ANALYSIS)
        self.db.commit()
        self.assertEqual(get_used(self.db, user_id=self.user.id, kind=KIND_ANALYSIS), 0)
        # 再次 refund 不会变成 -1
        refund(self.db, user_id=self.user.id, kind=KIND_ANALYSIS)
        self.db.commit()
        self.assertEqual(get_used(self.db, user_id=self.user.id, kind=KIND_ANALYSIS), 0)

    def test_kinds_are_isolated(self):
        cfg = QuotaConfig(daily_limit=2)
        try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg)
        try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg)
        self.db.commit()
        # analysis 耗尽不影响 agent
        self.assertFalse(try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg))
        self.assertTrue(try_consume(self.db, user_id=self.user.id, kind=KIND_AGENT, config=cfg))

    def test_dates_are_isolated(self):
        cfg = QuotaConfig(daily_limit=1)
        yesterday = date.today() - timedelta(days=1)
        # 昨日扣过一次
        try_consume(
            self.db,
            user_id=self.user.id,
            kind=KIND_ANALYSIS,
            config=cfg,
            on_date=yesterday,
        )
        self.db.commit()
        # 今日仍可继续扣
        today = date.today()
        self.assertTrue(
            try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg, on_date=today)
        )

    def test_invalid_kind_raises(self):
        with self.assertRaises(ValueError):
            try_consume(self.db, user_id=self.user.id, kind="invalid", config=QuotaConfig(1))

    def test_snapshot_aggregates_both_kinds(self):
        cfg = QuotaConfig(daily_limit=3)
        try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg)
        try_consume(self.db, user_id=self.user.id, kind=KIND_ANALYSIS, config=cfg)
        try_consume(self.db, user_id=self.user.id, kind=KIND_AGENT, config=cfg)
        self.db.commit()
        snap = get_quota_snapshot(
            self.db,
            user_id=self.user.id,
            analysis_limit=3,
            agent_limit=3,
        )
        self.assertEqual(snap.analysis_used, 2)
        self.assertEqual(snap.agent_used, 1)
        self.assertEqual(snap.analysis_remaining, 1)
        self.assertEqual(snap.agent_remaining, 2)

    def test_snapshot_unlimited_returns_none(self):
        snap = get_quota_snapshot(
            self.db,
            user_id=self.user.id,
            analysis_limit=0,
            agent_limit=0,
        )
        self.assertIsNone(snap.analysis_remaining)
        self.assertIsNone(snap.agent_remaining)

    def test_notify_kind_supported(self):
        cfg = QuotaConfig(daily_limit=1)
        self.assertTrue(try_consume(self.db, user_id=self.user.id, kind=KIND_NOTIFY, config=cfg))
        self.db.commit()
        self.assertFalse(try_consume(self.db, user_id=self.user.id, kind=KIND_NOTIFY, config=cfg))


if __name__ == "__main__":
    unittest.main()
