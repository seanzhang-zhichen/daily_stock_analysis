# -*- coding: utf-8 -*-
"""Phase 2 plans/redeem + Phase 4 BYOK 单元测试。"""

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
    AppUserByokCredential,
    Base,
)
from src.users.byok import (
    decrypt_secret,
    delete_credential,
    encrypt_secret,
    get_decrypted_key,
    list_credentials,
    upsert_credential,
)
from src.users.config import UserModeSettings
from src.users.errors import UserError
from src.users.passwords import hash_password
from src.users.plans import (
    grant_plan,
    redeem_code,
    resolve_user_plan,
)


def _enabled_settings() -> UserModeSettings:
    return UserModeSettings(
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
            email="byok@example.com",
            password_hash=hash_password("pw12345678"),
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()


class TestByokCrypto(unittest.TestCase):
    def test_encrypt_roundtrip_xor_fallback(self):
        os.environ.pop("DATA_ENCRYPTION_KEY", None)
        os.environ["USER_BYOK_FALLBACK_KEY"] = "unit-test-key"
        try:
            blob = encrypt_secret("sk-abc-1234567890")
            self.assertTrue(blob.startswith("v1:xorhmac:") or blob.startswith("v1:fernet:"))
            self.assertEqual(decrypt_secret(blob), "sk-abc-1234567890")
        finally:
            os.environ.pop("USER_BYOK_FALLBACK_KEY", None)

    def test_decrypt_rejects_tampered(self):
        os.environ["USER_BYOK_FALLBACK_KEY"] = "unit-test-key"
        try:
            blob = encrypt_secret("sk-tamper")
            if not blob.startswith("v1:xorhmac:"):
                self.skipTest("fernet 模式不走 xorhmac 路径")
            tampered = blob[:-2] + ("AA" if blob[-2:] != "AA" else "BB")
            with self.assertRaises(UserError):
                decrypt_secret(tampered)
        finally:
            os.environ.pop("USER_BYOK_FALLBACK_KEY", None)

    def test_empty_plaintext(self):
        self.assertEqual(encrypt_secret(""), "")
        self.assertEqual(decrypt_secret(""), "")


class TestByokCrud(_Base):
    def setUp(self) -> None:
        super().setUp()
        os.environ["USER_BYOK_FALLBACK_KEY"] = "unit-test-key"

    def tearDown(self) -> None:
        os.environ.pop("USER_BYOK_FALLBACK_KEY", None)
        super().tearDown()

    def test_upsert_and_get(self):
        view = upsert_credential(
            self.db,
            user=self.user,
            provider="openai",
            api_key="sk-orig-12345abcd",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
        )
        self.db.commit()
        self.assertEqual(view.provider, "openai")
        self.assertEqual(view.status, "active")
        self.assertTrue(view.key_preview.endswith("abcd"))
        plain = get_decrypted_key(self.db, user_id=self.user.id, provider="openai")
        self.assertEqual(plain, "sk-orig-12345abcd")

        # update: 第二次 upsert 覆盖
        upsert_credential(
            self.db,
            user=self.user,
            provider="openai",
            api_key="sk-new-67890wxyz",
        )
        self.db.commit()
        plain2 = get_decrypted_key(self.db, user_id=self.user.id, provider="openai")
        self.assertEqual(plain2, "sk-new-67890wxyz")
        # 只保留一行
        rows = self.db.query(AppUserByokCredential).filter_by(user_id=self.user.id).all()
        self.assertEqual(len(rows), 1)

    def test_rejects_unsupported_provider(self):
        with self.assertRaises(UserError):
            upsert_credential(
                self.db,
                user=self.user,
                provider="megaproviderx",
                api_key="sk-x",
            )

    def test_empty_key_rejected(self):
        with self.assertRaises(UserError):
            upsert_credential(self.db, user=self.user, provider="openai", api_key="   ")

    def test_list_does_not_expose_plaintext(self):
        upsert_credential(self.db, user=self.user, provider="openai", api_key="sk-secret-key1")
        upsert_credential(self.db, user=self.user, provider="anspire", api_key="sk-secret-key2")
        self.db.commit()
        views = list_credentials(self.db, user_id=self.user.id)
        self.assertEqual(len(views), 2)
        for v in views:
            self.assertNotIn("sk-secret", v.key_preview)
            self.assertTrue(v.key_preview.startswith("***"))

    def test_delete(self):
        upsert_credential(self.db, user=self.user, provider="openai", api_key="sk-del")
        self.db.commit()
        self.assertTrue(delete_credential(self.db, user_id=self.user.id, provider="openai"))
        self.db.commit()
        self.assertFalse(delete_credential(self.db, user_id=self.user.id, provider="openai"))
        self.assertIsNone(get_decrypted_key(self.db, user_id=self.user.id, provider="openai"))


class TestPlansAndRedeem(_Base):
    def setUp(self) -> None:
        super().setUp()
        # 建立 Pro 套餐定义
        self.pro_plan = AppPlan(
            code="pro",
            name="Pro",
            daily_analysis_limit=50,
            daily_agent_limit=50,
            max_stocks=30,
            can_byok=True,
            can_webhook=True,
            price_cents=2900,
        )
        self.db.add(self.pro_plan)
        self.db.commit()

    def test_resolve_plan_free_by_default(self):
        plan = resolve_user_plan(self.db, self.user, settings=_enabled_settings())
        self.assertEqual(plan.code, "free")
        self.assertEqual(plan.daily_analysis_limit, 5)
        self.assertEqual(plan.max_stocks, 3)
        self.assertFalse(plan.can_byok)

    def test_grant_plan_extends_expiry(self):
        sub = grant_plan(self.db, self.user, plan_code="pro", grant_days=30, source="manual")
        self.db.commit()
        self.assertIsInstance(sub, AppSubscription)
        self.assertEqual(self.user.plan_code, "pro")
        self.assertIsNotNone(self.user.plan_expires_at)

        plan = resolve_user_plan(self.db, self.user, settings=_enabled_settings())
        self.assertEqual(plan.code, "pro")
        self.assertEqual(plan.daily_analysis_limit, 50)
        self.assertTrue(plan.can_byok)

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
        plan = resolve_user_plan(self.db, self.user, settings=_enabled_settings())
        self.assertEqual(plan.code, "free")


if __name__ == "__main__":
    unittest.main()
