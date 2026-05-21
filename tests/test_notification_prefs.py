# -*- coding: utf-8 -*-
"""``src/users/notification_prefs`` 单元测试。

覆盖:
- update_prefs 免费档开启邮件通知被拒绝 (PERMISSION_DENIED)
- update_prefs 免费档开启每日推送被拒绝 (PERMISSION_DENIED)
- update_prefs 免费档关闭邮件通知允许 (仅变更 False 不需要 Pro)
- update_prefs Pro 档开启邮件通知和每日推送正常写入
- update_prefs 免费档尝试设置 webhook_url 被拒绝 (已有测试语义, 回归确认)
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import AppUser, AppUserNotificationPref, Base
from src.users.errors import UserError, UserErrorCode
from src.users.notification_prefs import get_prefs, update_prefs
from src.users.passwords import hash_password


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session()


def _add_user(db) -> AppUser:
    user = AppUser(email="test@example.com", password_hash=hash_password("pw12345678"))
    db.add(user)
    db.flush()
    return user


class TestEmailNotificationProGating(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _make_db()
        self.user = _add_user(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_free_user_cannot_enable_email(self):
        with self.assertRaises(UserError) as ctx:
            update_prefs(self.db, user_id=self.user.id, email_enabled=True, can_email_notifications=False)
        self.assertEqual(ctx.exception.code, UserErrorCode.PERMISSION_DENIED)
        self.assertIn("Pro", ctx.exception.message)

    def test_free_user_cannot_enable_daily_push(self):
        with self.assertRaises(UserError) as ctx:
            update_prefs(self.db, user_id=self.user.id, daily_push_enabled=True, can_email_notifications=False)
        self.assertEqual(ctx.exception.code, UserErrorCode.PERMISSION_DENIED)
        self.assertIn("Pro", ctx.exception.message)

    def test_free_user_can_disable_email(self):
        row = AppUserNotificationPref(user_id=self.user.id, email_enabled=True)
        self.db.add(row)
        self.db.flush()
        prefs = update_prefs(self.db, user_id=self.user.id, email_enabled=False, can_email_notifications=False)
        self.assertFalse(prefs.email_enabled)

    def test_free_user_can_disable_daily_push(self):
        row = AppUserNotificationPref(user_id=self.user.id, daily_push_enabled=True)
        self.db.add(row)
        self.db.flush()
        prefs = update_prefs(self.db, user_id=self.user.id, daily_push_enabled=False, can_email_notifications=False)
        self.assertFalse(prefs.daily_push_enabled)

    def test_pro_user_can_enable_email(self):
        prefs = update_prefs(self.db, user_id=self.user.id, email_enabled=True, can_email_notifications=True)
        self.assertTrue(prefs.email_enabled)

    def test_pro_user_can_enable_daily_push(self):
        prefs = update_prefs(self.db, user_id=self.user.id, daily_push_enabled=True, can_email_notifications=True)
        self.assertTrue(prefs.daily_push_enabled)

    def test_free_user_cannot_set_webhook(self):
        with self.assertRaises(UserError) as ctx:
            update_prefs(self.db, user_id=self.user.id, webhook_url="https://hook.example.com", can_webhook=False)
        self.assertEqual(ctx.exception.code, UserErrorCode.PERMISSION_DENIED)

    def test_get_prefs_returns_default_when_no_row(self):
        prefs = get_prefs(self.db, user_id=self.user.id)
        self.assertFalse(prefs.daily_push_enabled)
        self.assertTrue(prefs.email_enabled)
        self.assertIsNone(prefs.webhook_url)


if __name__ == "__main__":
    unittest.main()
