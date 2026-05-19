# -*- coding: utf-8 -*-
"""注册防刷护栏单元测试 (Phase 6 §5.8.1)。

覆盖三个核心场景:

1. 一次性邮箱黑名单拦截 (含 env 扩展 / 替换)。
2. 同邮箱滚动窗口内尝试超限。
3. 同 IP 滚动窗口内尝试超限。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import AppAuditLog, Base
from src.users.errors import UserError, UserErrorCode
from src.users.registration_guard import (
    DEFAULT_DISPOSABLE_DOMAINS,
    REGISTER_ATTEMPT_ACTION,
    REGISTER_BLOCKED_ACTION,
    RegistrationGuardConfig,
    extract_email_domain,
    is_disposable_email,
    load_disposable_domains,
    preflight_registration,
    record_registration_attempt,
)
from src.users.service import register_user
from tests.test_users_service import FakeEmailBackend, _enabled_settings


class _GuardTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()


class TestDisposableEmailHelpers(unittest.TestCase):
    def test_extract_email_domain_normalizes(self):
        self.assertEqual(extract_email_domain("Alice@Mailinator.COM"), "mailinator.com")
        self.assertEqual(extract_email_domain("  bob@example.com  "), "example.com")
        self.assertEqual(extract_email_domain("noatsign"), "")
        self.assertEqual(extract_email_domain(""), "")

    def test_is_disposable_email_uses_builtin_list(self):
        self.assertTrue(is_disposable_email("foo@mailinator.com"))
        self.assertTrue(is_disposable_email("foo@TEMP-MAIL.org"))
        self.assertFalse(is_disposable_email("foo@gmail.com"))
        self.assertFalse(is_disposable_email("foo@example.com"))

    def test_is_disposable_email_accepts_custom_blacklist(self):
        self.assertTrue(is_disposable_email("a@blocked.test", blacklist={"blocked.test"}))
        self.assertFalse(is_disposable_email("a@gmail.com", blacklist={"blocked.test"}))

    def test_load_disposable_domains_appends_extras(self):
        with mock.patch.dict(
            os.environ,
            {"USER_DISPOSABLE_EMAIL_DOMAINS": "fake.test, mock.test ,, MOCK.test"},
            clear=False,
        ):
            os.environ.pop("USER_DISPOSABLE_EMAIL_DOMAINS_REPLACE", None)
            domains = load_disposable_domains()
        # 内置黑名单仍然在内
        self.assertIn("mailinator.com", domains)
        # 新增并归一化为小写
        self.assertIn("fake.test", domains)
        self.assertIn("mock.test", domains)

    def test_load_disposable_domains_replace_mode(self):
        with mock.patch.dict(
            os.environ,
            {
                "USER_DISPOSABLE_EMAIL_DOMAINS": "only.test",
                "USER_DISPOSABLE_EMAIL_DOMAINS_REPLACE": "true",
            },
            clear=False,
        ):
            domains = load_disposable_domains()
        self.assertEqual(domains, frozenset({"only.test"}))
        self.assertNotIn("mailinator.com", domains)


class TestPreflightRegistration(_GuardTestBase):
    def test_preflight_blocks_disposable_email(self):
        with self.assertRaises(UserError) as ctx:
            preflight_registration(
                self.db,
                email="x@mailinator.com",
                ip="1.2.3.4",
                config=RegistrationGuardConfig(disposable_block_enabled=True),
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.INVALID_EMAIL)
        # 一条 blocked 审计落库
        blocked = (
            self.db.query(AppAuditLog)
            .filter(AppAuditLog.action == REGISTER_BLOCKED_ACTION)
            .all()
        )
        self.assertEqual(len(blocked), 1)
        self.assertIsNotNone(blocked[0].detail)
        self.assertIn("disposable_email", blocked[0].detail)

    def test_preflight_disabled_disposable_block(self):
        # 关闭 disposable 拦截后 mailinator 邮箱可以通过 preflight
        preflight_registration(
            self.db,
            email="x@mailinator.com",
            ip="1.2.3.4",
            config=RegistrationGuardConfig(
                disposable_block_enabled=False,
                ip_daily_max=0,
                email_daily_max=0,
            ),
        )

    def test_preflight_ip_rate_limit(self):
        cfg = RegistrationGuardConfig(
            disposable_block_enabled=False,
            ip_daily_max=2,
            email_daily_max=0,
        )
        # 模拟 2 次成功尝试 (记录后未被限流)
        for i in range(2):
            record_registration_attempt(
                self.db,
                email=f"user{i}@example.com",
                ip="9.9.9.9",
                user_agent="ua",
            )
            preflight_registration(
                self.db,
                email=f"user{i}@example.com",
                ip="9.9.9.9",
                config=cfg,
            )
        # 第 3 次: 先记录 (count -> 3 > 2), 再 preflight → 限流
        record_registration_attempt(
            self.db,
            email="user3@example.com",
            ip="9.9.9.9",
            user_agent="ua",
        )
        with self.assertRaises(UserError) as ctx:
            preflight_registration(
                self.db,
                email="user3@example.com",
                ip="9.9.9.9",
                config=cfg,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.RATE_LIMITED)

    def test_preflight_email_rate_limit(self):
        cfg = RegistrationGuardConfig(
            disposable_block_enabled=False,
            ip_daily_max=0,
            email_daily_max=2,
        )
        for i in range(2):
            record_registration_attempt(
                self.db,
                email="repeat@example.com",
                ip=f"1.1.1.{i}",
                user_agent="ua",
            )
            preflight_registration(
                self.db,
                email="repeat@example.com",
                ip=f"1.1.1.{i}",
                config=cfg,
            )
        record_registration_attempt(
            self.db,
            email="repeat@example.com",
            ip="1.1.1.99",
            user_agent="ua",
        )
        with self.assertRaises(UserError) as ctx:
            preflight_registration(
                self.db,
                email="repeat@example.com",
                ip="1.1.1.99",
                config=cfg,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.RATE_LIMITED)

    def test_preflight_zero_max_disables_check(self):
        # 限频 max=0 时直接放行, 不查计数
        cfg = RegistrationGuardConfig(
            disposable_block_enabled=False,
            ip_daily_max=0,
            email_daily_max=0,
        )
        for _ in range(100):
            record_registration_attempt(
                self.db,
                email="floor@example.com",
                ip="2.2.2.2",
                user_agent="ua",
            )
        # 100 次尝试都不会触发限流
        preflight_registration(
            self.db,
            email="floor@example.com",
            ip="2.2.2.2",
            config=cfg,
        )


class TestRegisterUserGuardIntegration(_GuardTestBase):
    """端到端验证 register_user 是否调用了护栏。"""

    def _settings(self, **overrides):
        return _enabled_settings(**overrides)

    def test_register_rejects_disposable_email(self):
        with self.assertRaises(UserError) as ctx:
            register_user(
                self.db,
                email="someone@mailinator.com",
                password="pw12345678",
                password_confirm="pw12345678",
                email_backend=FakeEmailBackend(),
                settings=self._settings(),
                terms_agreed=True,
                ip="10.0.0.1",
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.INVALID_EMAIL)
        # 审计日志: 1 条 attempt + 1 条 blocked
        attempts = (
            self.db.query(AppAuditLog)
            .filter(AppAuditLog.action == REGISTER_ATTEMPT_ACTION)
            .count()
        )
        blocked = (
            self.db.query(AppAuditLog)
            .filter(AppAuditLog.action == REGISTER_BLOCKED_ACTION)
            .count()
        )
        self.assertEqual(attempts, 1)
        self.assertEqual(blocked, 1)

    def test_register_rate_limits_after_too_many_email_attempts(self):
        settings = self._settings(
            register_email_daily_max=1,
            register_ip_daily_max=0,
            register_disposable_block=False,
        )
        # 第一次成功 (允许 1 次)
        register_user(
            self.db,
            email="rate@example.com",
            password="pw12345678",
            password_confirm="pw12345678",
            email_backend=FakeEmailBackend(),
            settings=settings,
            terms_agreed=True,
            ip="11.0.0.1",
        )
        self.db.commit()
        # 第二次同邮箱: 触发邮箱限频, 不是 EMAIL_ALREADY_REGISTERED
        with self.assertRaises(UserError) as ctx:
            register_user(
                self.db,
                email="rate@example.com",
                password="pw12345678",
                password_confirm="pw12345678",
                email_backend=FakeEmailBackend(),
                settings=settings,
                terms_agreed=True,
                ip="11.0.0.1",
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.RATE_LIMITED)

    def test_register_rate_limits_after_too_many_ip_attempts(self):
        settings = self._settings(
            register_email_daily_max=0,
            register_ip_daily_max=1,
            register_disposable_block=False,
        )
        # 第 1 次注册成功 (允许 1 次)
        register_user(
            self.db,
            email="first@example.com",
            password="pw12345678",
            password_confirm="pw12345678",
            email_backend=FakeEmailBackend(),
            settings=settings,
            terms_agreed=True,
            ip="22.0.0.1",
        )
        self.db.commit()
        # 第 2 次换不同邮箱, 同 IP: 仍触发限流
        with self.assertRaises(UserError) as ctx:
            register_user(
                self.db,
                email="second@example.com",
                password="pw12345678",
                password_confirm="pw12345678",
                email_backend=FakeEmailBackend(),
                settings=settings,
                terms_agreed=True,
                ip="22.0.0.1",
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.RATE_LIMITED)


class TestBuiltinBlacklistSanity(unittest.TestCase):
    """对内置黑名单做最低限度的健壮性检查, 避免误伤合法邮箱。"""

    def test_common_consumer_providers_not_blacklisted(self):
        for email in [
            "alice@gmail.com",
            "bob@outlook.com",
            "carol@163.com",
            "dave@qq.com",
            "eve@protonmail.com",
            "frank@icloud.com",
            "grace@hotmail.com",
            "henry@yahoo.com",
            "ivy@foxmail.com",
            "jack@126.com",
        ]:
            self.assertFalse(
                is_disposable_email(email, blacklist=DEFAULT_DISPOSABLE_DOMAINS),
                msg=f"{email} 不应在内置黑名单中",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
