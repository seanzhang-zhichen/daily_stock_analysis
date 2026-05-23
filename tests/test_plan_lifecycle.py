# -*- coding: utf-8 -*-
"""Phase 2 + Phase 4 收尾: ``src/users/plan_lifecycle`` 单元测试。

覆盖:
- ``find_users_needing_reminder``: 按 7/3/1 桶匹配, 跳过过期 / free 用户; 5 天剩余落到 7d 桶。
- ``send_renewal_reminder``: 首次发送写一条 ``AppPlanReminder`` + 审计, 重复触发幂等跳过。
- ``find_expired_active_users`` + ``downgrade_expired_user``: 自动降级 + 写订阅 + 邮件 + 审计。
- ``run_plan_lifecycle_check``: 一轮串联, 汇总 reminders / downgrades 计数。
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import (
    AppAuditLog,
    AppPlan,
    AppPlanReminder,
    AppSubscription,
    AppUser,
    Base,
)
from src.users.passwords import hash_password
from src.users.plan_lifecycle import (
    REMINDER_TYPE_EXPIRED,
    downgrade_expired_user,
    find_expired_active_users,
    find_users_needing_reminder,
    run_plan_lifecycle_check,
    send_renewal_reminder,
)


class _RecordingEmailBackend:
    """A minimal EmailBackend stub recording calls without side effects."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent = []

    def send(self, message) -> None:
        if self.fail:
            raise RuntimeError("smtp simulated failure")
        self.sent.append(message)


class _SessionScope:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


def _fake_module(name: str, **attrs):
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.now = datetime(2026, 5, 18, 12, 0, 0)

        plan = AppPlan(
            code="pro",
            name="Pro 月付",
            daily_analysis_limit=50,
            daily_agent_limit=50,
            max_stocks=30,
            can_webhook=True,
            price_cents=2900,
        )
        self.db.add(plan)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _make_user(
        self,
        email: str,
        *,
        plan_code: str = "pro",
        plan_expires_at=None,
        status: str = "active",
    ) -> AppUser:
        user = AppUser(
            email=email,
            password_hash=hash_password("pw12345678"),
            plan_code=plan_code,
            plan_expires_at=plan_expires_at,
            status=status,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user


class TestFindCandidates(_Base):
    def test_skips_free_user(self):
        self._make_user("free@x.com", plan_code="free", plan_expires_at=None)
        result = find_users_needing_reminder(self.db, now=self.now)
        self.assertEqual(result, [])

    def test_skips_disabled_user(self):
        self._make_user(
            "blocked@x.com",
            plan_expires_at=self.now + timedelta(days=2),
            status="disabled",
        )
        result = find_users_needing_reminder(self.db, now=self.now)
        self.assertEqual(result, [])

    def test_skips_expired_user(self):
        self._make_user(
            "expired@x.com",
            plan_expires_at=self.now - timedelta(hours=1),
        )
        result = find_users_needing_reminder(self.db, now=self.now)
        self.assertEqual(result, [])

    def test_matches_within_7d_bucket(self):
        # 剩余 5 天: 不在 [1,3] 但 ≤ 7, 应落到 7d 桶
        self._make_user(
            "five@x.com",
            plan_expires_at=self.now + timedelta(days=5),
        )
        # 剩余 2 天: 应落到 3d 桶
        self._make_user(
            "two@x.com",
            plan_expires_at=self.now + timedelta(days=2),
        )
        # 剩余 12 小时: 应落到 1d 桶 (向上取整)
        self._make_user(
            "half@x.com",
            plan_expires_at=self.now + timedelta(hours=12),
        )
        # 剩余 30 天: 超过最大 offset, 不进桶
        self._make_user(
            "far@x.com",
            plan_expires_at=self.now + timedelta(days=30),
        )

        result = find_users_needing_reminder(self.db, now=self.now)
        by_email = {c.user.email: c.reminder_type for c in result}
        self.assertEqual(by_email["five@x.com"], "expiring_7d")
        self.assertEqual(by_email["two@x.com"], "expiring_3d")
        self.assertEqual(by_email["half@x.com"], "expiring_1d")
        self.assertNotIn("far@x.com", by_email)


class TestSendRenewalReminder(_Base):
    def test_first_send_writes_record_and_audit(self):
        user = self._make_user(
            "remind@x.com",
            plan_expires_at=self.now + timedelta(days=2),
        )
        candidates = find_users_needing_reminder(self.db, now=self.now)
        self.assertEqual(len(candidates), 1)

        backend = _RecordingEmailBackend()
        ok = send_renewal_reminder(self.db, candidates[0], email_backend=backend)
        self.assertTrue(ok)
        self.assertEqual(len(backend.sent), 1)
        msg = backend.sent[0]
        self.assertEqual(msg.to, "remind@x.com")
        self.assertIn("Pro 月付", msg.subject)

        rows = self.db.query(AppPlanReminder).filter(AppPlanReminder.user_id == user.id).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].reminder_type, "expiring_3d")

        audits = (
            self.db.query(AppAuditLog)
            .filter(AppAuditLog.action == "plan.reminder_sent", AppAuditLog.user_id == user.id)
            .all()
        )
        self.assertEqual(len(audits), 1)

    def test_duplicate_send_is_idempotent(self):
        user = self._make_user(
            "remind2@x.com",
            plan_expires_at=self.now + timedelta(days=2),
        )
        candidates = find_users_needing_reminder(self.db, now=self.now)
        backend = _RecordingEmailBackend()
        first = send_renewal_reminder(self.db, candidates[0], email_backend=backend)
        second = send_renewal_reminder(self.db, candidates[0], email_backend=backend)
        self.assertTrue(first)
        self.assertFalse(second)
        # 第二次不应再发邮件
        self.assertEqual(len(backend.sent), 1)
        rows = self.db.query(AppPlanReminder).filter(AppPlanReminder.user_id == user.id).all()
        self.assertEqual(len(rows), 1)

    def test_email_failure_does_not_write_record(self):
        self._make_user(
            "fail@x.com",
            plan_expires_at=self.now + timedelta(days=2),
        )
        candidates = find_users_needing_reminder(self.db, now=self.now)
        backend = _RecordingEmailBackend(fail=True)
        ok = send_renewal_reminder(self.db, candidates[0], email_backend=backend)
        self.assertFalse(ok)
        # 邮件失败时不应写 reminder 记录, 下次调度还能重试
        rows = self.db.query(AppPlanReminder).all()
        self.assertEqual(rows, [])


class TestDowngrade(_Base):
    def test_find_expired_active_users(self):
        self._make_user(
            "expired@x.com",
            plan_expires_at=self.now - timedelta(hours=1),
        )
        self._make_user(
            "active@x.com",
            plan_expires_at=self.now + timedelta(days=10),
        )
        result = find_expired_active_users(self.db, now=self.now)
        self.assertEqual([u.email for u in result], ["expired@x.com"])

    def test_downgrade_writes_subscription_email_audit(self):
        user = self._make_user(
            "down@x.com",
            plan_expires_at=self.now - timedelta(hours=1),
        )
        backend = _RecordingEmailBackend()
        ok = downgrade_expired_user(self.db, user, email_backend=backend, now=self.now)
        self.assertTrue(ok)

        self.db.refresh(user)
        self.assertEqual(user.plan_code, "free")
        self.assertIsNone(user.plan_expires_at)

        subs = (
            self.db.query(AppSubscription)
            .filter(AppSubscription.user_id == user.id, AppSubscription.source == "expire")
            .all()
        )
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].plan_code, "free")

        reminders = (
            self.db.query(AppPlanReminder)
            .filter(
                AppPlanReminder.user_id == user.id,
                AppPlanReminder.reminder_type == REMINDER_TYPE_EXPIRED,
            )
            .all()
        )
        self.assertEqual(len(reminders), 1)

        audits = (
            self.db.query(AppAuditLog)
            .filter(AppAuditLog.action == "plan.auto_downgrade", AppAuditLog.user_id == user.id)
            .all()
        )
        self.assertEqual(len(audits), 1)

        self.assertEqual(len(backend.sent), 1)
        self.assertIn("已到期", backend.sent[0].subject)

    def test_downgrade_skips_when_already_free(self):
        user = self._make_user(
            "already@x.com",
            plan_code="free",
            plan_expires_at=None,
        )
        backend = _RecordingEmailBackend()
        ok = downgrade_expired_user(self.db, user, email_backend=backend, now=self.now)
        self.assertFalse(ok)
        self.assertEqual(backend.sent, [])

    def test_downgrade_dry_run_skips_writes(self):
        user = self._make_user(
            "dry@x.com",
            plan_expires_at=self.now - timedelta(hours=1),
        )
        backend = _RecordingEmailBackend()
        ok = downgrade_expired_user(
            self.db, user, email_backend=backend, now=self.now, dry_run=True
        )
        self.assertFalse(ok)
        self.db.refresh(user)
        self.assertEqual(user.plan_code, "pro")  # 未实际降级
        self.assertEqual(self.db.query(AppSubscription).count(), 0)
        self.assertEqual(self.db.query(AppPlanReminder).count(), 0)


class TestRunLifecycleCheck(_Base):
    def test_run_aggregates_reminders_and_downgrades(self):
        # 需要发提醒
        self._make_user(
            "soon@x.com",
            plan_expires_at=self.now + timedelta(days=2),
        )
        # 需要降级
        expired = self._make_user(
            "expired@x.com",
            plan_expires_at=self.now - timedelta(hours=1),
        )
        # 无操作: 距到期 30 天
        self._make_user(
            "far@x.com",
            plan_expires_at=self.now + timedelta(days=30),
        )

        backend = _RecordingEmailBackend()
        summary = run_plan_lifecycle_check(
            db=self.db, email_backend=backend, now=self.now
        )
        self.assertEqual(summary.reminders_sent, 1)
        self.assertEqual(summary.downgraded, 1)
        self.assertEqual(summary.reminders_skipped, 0)
        self.assertEqual(summary.downgrade_skipped, 0)
        # 一封提醒 + 一封降级邮件
        self.assertEqual(len(backend.sent), 2)

        # 再跑一次, 不应重复发送任何邮件
        summary2 = run_plan_lifecycle_check(
            db=self.db, email_backend=backend, now=self.now
        )
        self.assertEqual(summary2.reminders_sent, 0)
        self.assertEqual(summary2.downgraded, 0)
        self.assertEqual(len(backend.sent), 2)

        # 已降级用户应稳定在 free 档
        self.db.refresh(expired)
        self.assertEqual(expired.plan_code, "free")


class TestPerUserScheduledAnalysis(unittest.TestCase):
    def test_skips_non_pro_user_before_running_pipeline(self):
        import backend.main as main

        pipeline_cls = MagicMock()
        get_db = MagicMock(return_value=SimpleNamespace(session_scope=lambda: _SessionScope()))
        get_users_with_daily_push = MagicMock(return_value=[7])
        get_user_by_id = MagicMock(return_value=SimpleNamespace(id=7, email="u@example.com", status="active"))
        list_stocks = MagicMock(return_value=[SimpleNamespace(stock_code="600519")])
        get_prefs = MagicMock(return_value=SimpleNamespace(email_enabled=True, webhook_url=None, webhook_type=None))
        resolve_user_plan = MagicMock(return_value=SimpleNamespace(is_pro=False, can_webhook=False))

        fake_modules = {
            "src.core.pipeline": _fake_module("src.core.pipeline", StockAnalysisPipeline=pipeline_cls),
            "src.notification": _fake_module("src.notification", NotificationService=MagicMock()),
            "src.storage": _fake_module("src.storage", get_db=get_db),
            "src.users.config": _fake_module(
                "src.users.config",
                is_user_mode_enabled=MagicMock(return_value=True),
                load_user_mode_settings=MagicMock(return_value=SimpleNamespace()),
            ),
            "src.users.email": _fake_module("src.users.email", get_email_backend=MagicMock(return_value=object())),
            "src.users.notification_delivery": _fake_module(
                "src.users.notification_delivery",
                DailyEmailContext=MagicMock(),
                dispatch_user_webhook=MagicMock(),
                send_daily_email=MagicMock(),
            ),
            "src.users.notification_prefs": _fake_module(
                "src.users.notification_prefs",
                get_prefs=get_prefs,
                get_users_with_daily_push=get_users_with_daily_push,
            ),
            "src.users.plans": _fake_module("src.users.plans", resolve_user_plan=resolve_user_plan),
            "src.users.repository": _fake_module("src.users.repository", get_user_by_id=get_user_by_id),
            "src.users.watchlist": _fake_module("src.users.watchlist", list_stocks=list_stocks),
        }

        with patch.dict(sys.modules, fake_modules):
            main.run_per_user_scheduled_analysis(
                SimpleNamespace(report_type="simple"),
                SimpleNamespace(workers=1, dry_run=False),
            )

        pipeline_cls.assert_not_called()
        get_users_with_daily_push.assert_called_once()
        get_user_by_id.assert_called_once()
        list_stocks.assert_called_once()
        get_prefs.assert_called_once()
        resolve_user_plan.assert_called_once()


if __name__ == "__main__":
    unittest.main()
