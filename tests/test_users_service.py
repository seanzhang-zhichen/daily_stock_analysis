# -*- coding: utf-8 -*-
"""Phase 1 To C 用户体系单元测试。

覆盖范围:
- 密码哈希 / 验证 / 强度
- 邮箱合法性
- session 颁发 / 解析 / 吊销
- 注册 / 登录 / 邮箱验证 / 密码重置 / 改密 / 限速

测试使用内存 SQLite, 不依赖项目运行时配置。
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta
from typing import Optional

# Ensure project root on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.storage import (
    AppUser,
    AppUserEmailVerification,
    AppUserSession,
    Base,
)
from src.users import repository as repo
from src.users.config import UserModeSettings
from src.users.email import EmailMessageDTO
from src.users.errors import UserError, UserErrorCode
from src.users.passwords import (
    hash_password,
    hash_token,
    is_valid_email,
    validate_password_strength,
    verify_password,
)
from src.users.sessions import (
    issue_session,
    resolve_session,
    revoke_all_user_sessions,
    revoke_session,
)
from src.users.service import (
    change_password,
    login,
    register_user,
    request_password_reset,
    reset_password,
    verify_email,
)


def _enabled_settings(**overrides) -> UserModeSettings:
    base = dict(
        enabled=True,
        public_registration_enabled=True,
        require_email_verification=False,
        session_ttl_hours=24,
        verification_ttl_hours=24,
        reset_ttl_hours=2,
        free_daily_analysis=5,
        free_daily_agent=5,
        free_max_stocks=3,
        invite_codes=(),
        register_disposable_block=True,
        register_ip_daily_max=10,
        register_email_daily_max=3,
        register_rate_window_hours=24,
    )
    base.update(overrides)
    return UserModeSettings(**base)


class FakeEmailBackend:
    """In-memory email backend that records the latest message + token."""

    def __init__(self) -> None:
        self.sent: list[EmailMessageDTO] = []

    def send(self, message: EmailMessageDTO) -> None:
        self.sent.append(message)

    def last_token(self) -> Optional[str]:
        """Extract the last token from the most recent email body."""
        if not self.sent:
            return None
        body = self.sent[-1].body_text
        # Tokens are emitted on their own line, surrounded by blank lines.
        for line in body.splitlines():
            line = line.strip()
            # token_urlsafe(32) => 43 chars, base64url alphabet
            if len(line) >= 30 and all(c.isalnum() or c in "-_" for c in line):
                return line
        return None


class _UsersTestBase(unittest.TestCase):
    def setUp(self) -> None:
        # In-memory SQLite, schema created from Base metadata (uses our new tables).
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        self.email_backend = FakeEmailBackend()
        self.settings = _enabled_settings()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()


class TestPasswordsAndEmail(unittest.TestCase):
    def test_password_hash_roundtrip(self):
        hashed = hash_password("Sup3rSecret!")
        self.assertTrue(verify_password("Sup3rSecret!", hashed))
        self.assertFalse(verify_password("wrong", hashed))
        self.assertFalse(verify_password("", hashed))

    def test_password_hash_format(self):
        hashed = hash_password("anything12")
        parts = hashed.split("$")
        self.assertEqual(parts[0], "pbkdf2_sha256")
        self.assertEqual(len(parts), 4)
        self.assertGreater(int(parts[1]), 0)

    def test_password_strength(self):
        self.assertIsNone(validate_password_strength("longenough"))
        self.assertIsNotNone(validate_password_strength(""))
        self.assertIsNotNone(validate_password_strength("short"))
        self.assertIsNotNone(validate_password_strength("x" * 200))

    def test_email_validation(self):
        self.assertTrue(is_valid_email("alice@example.com"))
        self.assertTrue(is_valid_email("  alice@example.com  "))
        self.assertFalse(is_valid_email(""))
        self.assertFalse(is_valid_email("not-an-email"))
        self.assertFalse(is_valid_email("a@b"))

    def test_token_hash_stable(self):
        self.assertEqual(hash_token("abc"), hash_token("abc"))
        self.assertNotEqual(hash_token("abc"), hash_token("xyz"))
        self.assertEqual(hash_token(""), "")


class TestSessions(_UsersTestBase):
    def _make_user(self) -> AppUser:
        user = repo.create_user(
            self.db,
            email="alice@example.com",
            password_hash=hash_password("pw12345678"),
        )
        self.db.commit()
        return user

    def test_issue_and_resolve(self):
        user = self._make_user()
        issued = issue_session(self.db, user, ttl_hours=24)
        self.db.commit()
        self.assertIsNotNone(issued.cookie_value)
        resolved = resolve_session(self.db, issued.cookie_value)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, user.id)

    def test_resolve_rejects_unknown_or_empty(self):
        self.assertIsNone(resolve_session(self.db, ""))
        self.assertIsNone(resolve_session(self.db, "not-a-real-token"))

    def test_revoke_session_blocks_future_resolve(self):
        user = self._make_user()
        issued = issue_session(self.db, user, ttl_hours=24)
        self.db.commit()
        self.assertTrue(revoke_session(self.db, issued.cookie_value))
        self.db.commit()
        self.assertIsNone(resolve_session(self.db, issued.cookie_value))

    def test_revoke_all_user_sessions(self):
        user = self._make_user()
        a = issue_session(self.db, user, ttl_hours=24)
        b = issue_session(self.db, user, ttl_hours=24)
        self.db.commit()
        count = revoke_all_user_sessions(self.db, user.id)
        self.db.commit()
        self.assertEqual(count, 2)
        self.assertIsNone(resolve_session(self.db, a.cookie_value))
        self.assertIsNone(resolve_session(self.db, b.cookie_value))

    def test_resolve_rejects_disabled_user(self):
        user = self._make_user()
        issued = issue_session(self.db, user, ttl_hours=24)
        user.status = "disabled"
        self.db.add(user)
        self.db.commit()
        self.assertIsNone(resolve_session(self.db, issued.cookie_value))

    def test_resolve_rejects_expired_session(self):
        user = self._make_user()
        issued = issue_session(self.db, user, ttl_hours=24)
        # Backdate expiry so the session is now considered expired.
        row = (
            self.db.query(AppUserSession)
            .filter(AppUserSession.token_hash == hash_token(issued.cookie_value))
            .first()
        )
        assert row is not None
        row.expires_at = datetime.utcnow() - timedelta(hours=1)
        self.db.add(row)
        self.db.commit()
        self.assertIsNone(resolve_session(self.db, issued.cookie_value))


class TestRegistration(_UsersTestBase):
    def test_register_success_issues_session_when_no_verification(self):
        result = register_user(
            self.db,
            email="bob@example.com",
            password="pw12345678",
            password_confirm="pw12345678",
            email_backend=self.email_backend,
            settings=self.settings,
            terms_agreed=True,
        )
        self.db.commit()
        self.assertFalse(result.requires_verification)
        self.assertIsNotNone(result.issued_session)
        self.assertEqual(result.user.email, "bob@example.com")
        self.assertIsNotNone(result.user.email_verified_at)  # auto-verified
        self.assertIsNotNone(result.user.last_login_at)

    def test_register_requires_verification_skips_session(self):
        settings = _enabled_settings(require_email_verification=True)
        result = register_user(
            self.db,
            email="carol@example.com",
            password="pw12345678",
            password_confirm="pw12345678",
            email_backend=self.email_backend,
            settings=settings,
            terms_agreed=True,
        )
        self.db.commit()
        self.assertTrue(result.requires_verification)
        self.assertIsNone(result.issued_session)
        self.assertIsNone(result.user.email_verified_at)
        # 验证邮件被发送了
        self.assertEqual(len(self.email_backend.sent), 1)
        token = self.email_backend.last_token()
        self.assertIsNotNone(token)

        # 用 token 完成验证后, 用户 email_verified_at 不再为空
        verified_user = verify_email(self.db, token=token, settings=settings)
        self.db.commit()
        self.assertIsNotNone(verified_user.email_verified_at)

    def test_register_password_mismatch(self):
        with self.assertRaises(UserError) as ctx:
            register_user(
                self.db,
                email="x@example.com",
                password="pw12345678",
                password_confirm="different1",
                email_backend=self.email_backend,
                settings=self.settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.PASSWORD_MISMATCH)

    def test_register_invalid_email(self):
        with self.assertRaises(UserError) as ctx:
            register_user(
                self.db,
                email="not-an-email",
                password="pw12345678",
                password_confirm="pw12345678",
                email_backend=self.email_backend,
                settings=self.settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.INVALID_EMAIL)

    def test_register_duplicate_email(self):
        register_user(
            self.db,
            email="dup@example.com",
            password="pw12345678",
            password_confirm="pw12345678",
            email_backend=self.email_backend,
            settings=self.settings,
            terms_agreed=True,
        )
        self.db.commit()
        with self.assertRaises(UserError) as ctx:
            register_user(
                self.db,
                email="dup@example.com",
                password="pw12345678",
                password_confirm="pw12345678",
                email_backend=self.email_backend,
                settings=self.settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.EMAIL_ALREADY_REGISTERED)

    def test_register_requires_invite_when_configured(self):
        settings = _enabled_settings(invite_codes=("VIP",))
        with self.assertRaises(UserError) as ctx:
            register_user(
                self.db,
                email="needs-invite@example.com",
                password="pw12345678",
                password_confirm="pw12345678",
                email_backend=self.email_backend,
                settings=settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.INVITE_CODE_REQUIRED)

        with self.assertRaises(UserError) as ctx:
            register_user(
                self.db,
                email="bad-invite@example.com",
                password="pw12345678",
                password_confirm="pw12345678",
                invite_code="WRONG",
                email_backend=self.email_backend,
                settings=settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.INVITE_CODE_INVALID)

        result = register_user(
            self.db,
            email="good-invite@example.com",
            password="pw12345678",
            password_confirm="pw12345678",
            invite_code="VIP",
            email_backend=self.email_backend,
            settings=settings,
            terms_agreed=True,
        )
        self.db.commit()
        self.assertEqual(result.user.email, "good-invite@example.com")


class TestLoginAndPasswordFlows(_UsersTestBase):
    def _register(self, email: str = "user@example.com", password: str = "pw12345678") -> AppUser:
        result = register_user(
            self.db,
            email=email,
            password=password,
            password_confirm=password,
            email_backend=self.email_backend,
            settings=self.settings,
            terms_agreed=True,
        )
        self.db.commit()
        return result.user

    def test_login_success(self):
        self._register()
        issued = login(
            self.db,
            email="user@example.com",
            password="pw12345678",
            settings=self.settings,
        )
        self.db.commit()
        self.assertIsNotNone(issued.cookie_value)
        resolved = resolve_session(self.db, issued.cookie_value)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.email, "user@example.com")

    def test_login_wrong_password(self):
        self._register()
        with self.assertRaises(UserError) as ctx:
            login(
                self.db,
                email="user@example.com",
                password="wrong-password",
                settings=self.settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.INVALID_CREDENTIALS)

    def test_login_unknown_email(self):
        with self.assertRaises(UserError) as ctx:
            login(
                self.db,
                email="ghost@example.com",
                password="pw12345678",
                settings=self.settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.INVALID_CREDENTIALS)

    def test_login_blocks_unverified_when_required(self):
        settings = _enabled_settings(require_email_verification=True)
        register_user(
            self.db,
            email="pending@example.com",
            password="pw12345678",
            password_confirm="pw12345678",
            email_backend=self.email_backend,
            settings=settings,
            terms_agreed=True,
        )
        self.db.commit()
        with self.assertRaises(UserError) as ctx:
            login(
                self.db,
                email="pending@example.com",
                password="pw12345678",
                settings=settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.EMAIL_NOT_VERIFIED)

    def test_login_blocks_disabled_user(self):
        user = self._register()
        user.status = "disabled"
        self.db.add(user)
        self.db.commit()
        with self.assertRaises(UserError) as ctx:
            login(
                self.db,
                email=user.email,
                password="pw12345678",
                settings=self.settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.USER_DISABLED)

    def test_password_reset_flow_revokes_existing_sessions(self):
        self._register(email="reset@example.com", password="initialPw1")
        issued = login(
            self.db,
            email="reset@example.com",
            password="initialPw1",
            settings=self.settings,
        )
        self.db.commit()

        # 触发重置邮件
        request_password_reset(
            self.db,
            email="reset@example.com",
            email_backend=self.email_backend,
            settings=self.settings,
        )
        self.db.commit()
        token = self.email_backend.last_token()
        self.assertIsNotNone(token)

        # 用 token 重置密码
        reset_password(
            self.db,
            token=token,
            new_password="brandNewPw2",
            new_password_confirm="brandNewPw2",
            settings=self.settings,
        )
        self.db.commit()

        # 旧 session 应被吊销
        self.assertIsNone(resolve_session(self.db, issued.cookie_value))
        # 旧密码不能登录, 新密码能登录
        with self.assertRaises(UserError):
            login(
                self.db,
                email="reset@example.com",
                password="initialPw1",
                settings=self.settings,
            )
        relogin = login(
            self.db,
            email="reset@example.com",
            password="brandNewPw2",
            settings=self.settings,
        )
        self.db.commit()
        self.assertIsNotNone(relogin.cookie_value)

    def test_request_password_reset_unknown_email_is_silent(self):
        # 对未注册邮箱不应抛错误, 也不应发邮件
        request_password_reset(
            self.db,
            email="never-registered@example.com",
            email_backend=self.email_backend,
            settings=self.settings,
        )
        self.db.commit()
        self.assertEqual(len(self.email_backend.sent), 0)

    def test_reset_token_is_single_use(self):
        self._register(email="oneshot@example.com")
        request_password_reset(
            self.db,
            email="oneshot@example.com",
            email_backend=self.email_backend,
            settings=self.settings,
        )
        self.db.commit()
        token = self.email_backend.last_token()
        self.assertIsNotNone(token)
        reset_password(
            self.db,
            token=token,
            new_password="newPassword1",
            new_password_confirm="newPassword1",
            settings=self.settings,
        )
        self.db.commit()
        # 再用同一个 token 应失败
        with self.assertRaises(UserError) as ctx:
            reset_password(
                self.db,
                token=token,
                new_password="another1",
                new_password_confirm="another1",
                settings=self.settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.INVALID_TOKEN)

    def test_change_password_requires_current(self):
        user = self._register(email="chg@example.com", password="origPw12")
        with self.assertRaises(UserError) as ctx:
            change_password(
                self.db,
                user=user,
                current_password="wrong",
                new_password="newPw1234",
                new_password_confirm="newPw1234",
                settings=self.settings,
            )
        self.assertEqual(ctx.exception.code, UserErrorCode.INVALID_CREDENTIALS)

    def test_change_password_revokes_existing_sessions(self):
        user = self._register(email="chg2@example.com", password="origPw12")
        issued = login(
            self.db,
            email=user.email,
            password="origPw12",
            settings=self.settings,
        )
        self.db.commit()
        change_password(
            self.db,
            user=user,
            current_password="origPw12",
            new_password="newPw1234",
            new_password_confirm="newPw1234",
            settings=self.settings,
        )
        self.db.commit()
        self.assertIsNone(resolve_session(self.db, issued.cookie_value))
        # 新密码登录正常
        relogin = login(
            self.db,
            email=user.email,
            password="newPw1234",
            settings=self.settings,
        )
        self.db.commit()
        self.assertIsNotNone(relogin.cookie_value)


class TestVerificationToken(_UsersTestBase):
    def test_consume_token_marks_used(self):
        user = repo.create_user(
            self.db,
            email="verify@example.com",
            password_hash=hash_password("pw12345678"),
        )
        self.db.commit()
        raw_token = "abcdef123456-xyz"
        repo.create_verification_token(
            self.db,
            user_id=user.id,
            raw_token=raw_token,
            purpose="verify",
            ttl_hours=24,
        )
        self.db.commit()

        row = repo.consume_verification_token(
            self.db,
            raw_token=raw_token,
            purpose="verify",
        )
        self.db.commit()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row.consumed_at)

        # 再次消费应失败
        again = repo.consume_verification_token(
            self.db,
            raw_token=raw_token,
            purpose="verify",
        )
        self.assertIsNone(again)

    def test_consume_token_rejects_wrong_purpose(self):
        user = repo.create_user(
            self.db,
            email="wrong-purpose@example.com",
            password_hash=hash_password("pw12345678"),
        )
        self.db.commit()
        repo.create_verification_token(
            self.db,
            user_id=user.id,
            raw_token="token-1",
            purpose="verify",
            ttl_hours=24,
        )
        self.db.commit()
        row = repo.consume_verification_token(
            self.db,
            raw_token="token-1",
            purpose="reset",
        )
        self.assertIsNone(row)

    def test_consume_token_rejects_expired(self):
        user = repo.create_user(
            self.db,
            email="expired-token@example.com",
            password_hash=hash_password("pw12345678"),
        )
        self.db.commit()
        repo.create_verification_token(
            self.db,
            user_id=user.id,
            raw_token="token-exp",
            purpose="verify",
            ttl_hours=24,
        )
        # 把过期时间手动改成过去
        row = (
            self.db.query(AppUserEmailVerification)
            .filter(AppUserEmailVerification.token_hash == hash_token("token-exp"))
            .first()
        )
        assert row is not None
        row.expires_at = datetime.utcnow() - timedelta(hours=1)
        self.db.add(row)
        self.db.commit()
        consumed = repo.consume_verification_token(
            self.db,
            raw_token="token-exp",
            purpose="verify",
        )
        self.assertIsNone(consumed)


if __name__ == "__main__":
    unittest.main()
