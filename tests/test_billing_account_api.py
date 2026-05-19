# -*- coding: utf-8 -*-
"""Phase 2 商业化 endpoint 集成测试。

覆盖:
- ``/api/v1/billing/plans``: 未启用 To C 模式时优雅降级; 启用时返回兜底套餐目录。
- ``/api/v1/billing/subscription``: 未登录返回 401; 登录后返回当前 plan + 历史。
- ``/api/v1/account/redeem``: 兑换码合法 -> 升级到 Pro; 重复使用 -> 失败。
- ``/api/v1/account/api-keys``: free 用户无法写入; Pro 用户可以 CRUD。

测试用 SQLite + monkeypatched env 启用 To C 模式, 不依赖 LLM 运行时。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from typing import Optional
from unittest.mock import MagicMock

# Keep this test runnable when optional LLM runtime deps are not installed.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient

from src.config import Config
from src.storage import (
    AppPlan,
    AppRedeemCode,
    AppUser,
    AppUserByokCredential,
    DatabaseManager,
)
from src.users.passwords import hash_password
from src.users.sessions import issue_session


def _enable_user_mode(monkeypatch_env: dict) -> None:
    monkeypatch_env["USER_PUBLIC_REGISTRATION_ENABLED"] = "true"
    monkeypatch_env["USER_REQUIRE_EMAIL_VERIFICATION"] = "false"
    monkeypatch_env["USER_FREE_DAILY_ANALYSIS"] = "5"
    monkeypatch_env["USER_FREE_DAILY_AGENT"] = "5"
    monkeypatch_env["USER_FREE_MAX_STOCKS"] = "3"
    monkeypatch_env["USER_BYOK_FALLBACK_KEY"] = "unit-test-key"


class _BaseApi(unittest.TestCase):
    """Spin up a fresh FastAPI app + sqlite DB per test."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "to_c_api.db")

        # Stash + override env, restore in tearDown
        self._saved_env = {
            k: os.environ.get(k)
            for k in [
                "DATABASE_PATH",
                "USER_PUBLIC_REGISTRATION_ENABLED",
                "USER_REQUIRE_EMAIL_VERIFICATION",
                "USER_FREE_DAILY_ANALYSIS",
                "USER_FREE_DAILY_AGENT",
                "USER_FREE_MAX_STOCKS",
                "USER_BYOK_FALLBACK_KEY",
                "ADMIN_AUTH_ENABLED",
            ]
        }
        os.environ["DATABASE_PATH"] = self._db_path
        os.environ["ADMIN_AUTH_ENABLED"] = "false"
        _enable_user_mode(os.environ)

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db_manager = DatabaseManager.get_instance()
        self.SessionLocal = self.db_manager.get_session

        # Lazy import to honour the env override
        from api.app import create_app

        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._temp_dir.cleanup()

    # ----- helpers --------------------------------------------------------

    def _create_user(self, email: str = "u@example.com", *, plan_code: str = "free") -> AppUser:
        session = self.db_manager.get_session()
        try:
            user = AppUser(
                email=email,
                password_hash=hash_password("pw12345678"),
                plan_code=plan_code,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user
        finally:
            session.close()

    def _login(self, user: AppUser) -> None:
        """Issue a session for ``user`` and attach it to the test client."""
        session = self.db_manager.get_session()
        try:
            issued = issue_session(session, user, ttl_hours=24)
            session.commit()
        finally:
            session.close()
        self.client.cookies.set("dsa_user_session", issued.cookie_value)

    def _seed_pro_plan(self) -> AppPlan:
        session = self.db_manager.get_session()
        try:
            plan = AppPlan(
                code="pro",
                name="Pro",
                daily_analysis_limit=50,
                daily_agent_limit=50,
                max_stocks=30,
                can_byok=True,
                can_webhook=True,
                price_cents=2900,
            )
            session.add(plan)
            session.commit()
            session.refresh(plan)
            return plan
        finally:
            session.close()


class TestBillingPlans(_BaseApi):
    def test_plans_anonymous_returns_fallback_when_table_empty(self):
        res = self.client.get("/api/v1/billing/plans")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["userModeEnabled"])
        codes = {p["code"] for p in body["plans"]}
        self.assertIn("free", codes)
        self.assertIn("pro", codes)
        self.assertIsNone(body["currentPlan"])

    def test_plans_uses_db_rows_when_present(self):
        self._seed_pro_plan()
        res = self.client.get("/api/v1/billing/plans")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        codes = {p["code"] for p in body["plans"]}
        # 表中只塞了 pro, 应该返回 [pro] 而不是 fallback 列表
        self.assertEqual(codes, {"pro"})

    def test_plans_includes_current_plan_when_logged_in(self):
        self._seed_pro_plan()
        user = self._create_user()
        self._login(user)
        res = self.client.get("/api/v1/billing/plans")
        body = res.json()
        self.assertIsNotNone(body["currentPlan"])
        self.assertEqual(body["currentPlan"]["code"], "free")


class TestBillingSubscription(_BaseApi):
    def test_subscription_requires_login(self):
        res = self.client.get("/api/v1/billing/subscription")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"], "unauthorized")

    def test_subscription_returns_current_plan(self):
        user = self._create_user()
        self._login(user)
        res = self.client.get("/api/v1/billing/subscription")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["plan"]["code"], "free")
        self.assertFalse(body["plan"]["isActivePaid"])
        self.assertEqual(body["subscriptions"], [])


class TestRedeem(_BaseApi):
    def test_redeem_requires_login(self):
        res = self.client.post("/api/v1/account/redeem", json={"code": "ANY"})
        self.assertEqual(res.status_code, 401)
        # AuthMiddleware 会在 endpoint 之前拦截未登录请求, 返回 "unauthorized"
        self.assertEqual(res.json()["error"], "unauthorized")

    def test_redeem_unknown_code_fails(self):
        user = self._create_user()
        self._login(user)
        res = self.client.post("/api/v1/account/redeem", json={"code": "MISSING"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"], "invite_code_invalid")

    def test_redeem_valid_code_upgrades_to_pro(self):
        self._seed_pro_plan()
        # 写一个 30 天的 redeem code
        session = self.db_manager.get_session()
        try:
            session.add(AppRedeemCode(code="VIP-AAAA", plan_code="pro", grant_days=30))
            session.commit()
        finally:
            session.close()

        user = self._create_user()
        self._login(user)
        res = self.client.post("/api/v1/account/redeem", json={"code": "VIP-AAAA"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["plan"]["code"], "pro")
        self.assertTrue(body["plan"]["isPro"])
        self.assertTrue(body["plan"]["canByok"])

        # 再次兑换同一个 code 应失败
        res2 = self.client.post("/api/v1/account/redeem", json={"code": "VIP-AAAA"})
        self.assertEqual(res2.status_code, 400)
        self.assertEqual(res2.json()["error"], "invite_code_invalid")


class TestApiKeysEndpoint(_BaseApi):
    def test_list_requires_login(self):
        res = self.client.get("/api/v1/account/api-keys")
        self.assertEqual(res.status_code, 401)

    def test_free_user_cannot_upsert(self):
        user = self._create_user()
        self._login(user)
        res = self.client.post(
            "/api/v1/account/api-keys",
            json={"provider": "openai", "apiKey": "sk-abc-12345"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["error"], "registration_disabled")

    def test_pro_user_can_upsert_and_delete(self):
        self._seed_pro_plan()
        # 直接把用户开成 pro
        user = self._create_user()
        sess = self.db_manager.get_session()
        try:
            db_user = sess.query(AppUser).filter(AppUser.id == user.id).first()
            assert db_user is not None
            db_user.plan_code = "pro"
            db_user.plan_expires_at = datetime.utcnow() + timedelta(days=30)
            sess.add(db_user)
            sess.commit()
        finally:
            sess.close()

        self._login(user)

        # 列表 + canByok=True
        res_list = self.client.get("/api/v1/account/api-keys")
        self.assertEqual(res_list.status_code, 200)
        body = res_list.json()
        self.assertTrue(body["canByok"])
        self.assertEqual(body["credentials"], [])

        # upsert
        res = self.client.post(
            "/api/v1/account/api-keys",
            json={"provider": "openai", "apiKey": "sk-test-abcd1234"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        cred = res.json()["credential"]
        self.assertEqual(cred["provider"], "openai")
        self.assertEqual(cred["status"], "active")
        self.assertTrue(cred["keyPreview"].startswith("***"))

        # 列表里能看到
        res_list2 = self.client.get("/api/v1/account/api-keys")
        self.assertEqual(len(res_list2.json()["credentials"]), 1)

        # 删除
        res_del = self.client.delete("/api/v1/account/api-keys/openai")
        self.assertEqual(res_del.status_code, 204)
        res_del2 = self.client.delete("/api/v1/account/api-keys/openai")
        self.assertEqual(res_del2.status_code, 404)

    def test_unsupported_provider_rejected(self):
        self._seed_pro_plan()
        user = self._create_user()
        sess = self.db_manager.get_session()
        try:
            db_user = sess.query(AppUser).filter(AppUser.id == user.id).first()
            assert db_user is not None
            db_user.plan_code = "pro"
            db_user.plan_expires_at = datetime.utcnow() + timedelta(days=30)
            sess.add(db_user)
            sess.commit()
        finally:
            sess.close()
        self._login(user)
        res = self.client.post(
            "/api/v1/account/api-keys",
            json={"provider": "rogue_provider", "apiKey": "sk-x"},
        )
        self.assertEqual(res.status_code, 400)


class TestAccountStatusPlan(_BaseApi):
    def test_status_includes_plan_block_when_logged_in(self):
        user = self._create_user()
        self._login(user)
        res = self.client.get("/api/v1/account/status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["loggedIn"])
        self.assertIsNotNone(body["plan"])
        self.assertEqual(body["plan"]["code"], "free")
        self.assertFalse(body["plan"]["isPro"])

    def test_status_anonymous_has_no_plan(self):
        res = self.client.get("/api/v1/account/status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["loggedIn"])
        self.assertIsNone(body["plan"])
        self.assertIsNone(body["renewal"])

    def test_status_renewal_null_for_free_user(self):
        user = self._create_user()
        self._login(user)
        res = self.client.get("/api/v1/account/status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        # free 档无续费提示
        self.assertIsNone(body["renewal"])

    def test_status_renewal_flags_expiring_soon(self):
        self._seed_pro_plan()
        user = self._create_user()
        # 把用户升到 pro, 还剩 2 天
        sess = self.db_manager.get_session()
        try:
            db_user = sess.query(AppUser).filter(AppUser.id == user.id).first()
            assert db_user is not None
            db_user.plan_code = "pro"
            db_user.plan_expires_at = datetime.utcnow() + timedelta(days=2)
            sess.add(db_user)
            sess.commit()
        finally:
            sess.close()
        self._login(user)

        res = self.client.get("/api/v1/account/status")
        self.assertEqual(res.status_code, 200)
        renewal = res.json()["renewal"]
        self.assertIsNotNone(renewal)
        self.assertEqual(renewal["planCode"], "pro")
        self.assertTrue(renewal["willExpireSoon"])
        self.assertFalse(renewal["expired"])
        self.assertGreaterEqual(renewal["daysRemaining"], 1)
        self.assertLessEqual(renewal["daysRemaining"], 2)

    def test_status_renewal_flags_expired(self):
        self._seed_pro_plan()
        user = self._create_user()
        sess = self.db_manager.get_session()
        try:
            db_user = sess.query(AppUser).filter(AppUser.id == user.id).first()
            assert db_user is not None
            db_user.plan_code = "pro"
            db_user.plan_expires_at = datetime.utcnow() - timedelta(hours=1)
            sess.add(db_user)
            sess.commit()
        finally:
            sess.close()
        self._login(user)

        res = self.client.get("/api/v1/account/status")
        self.assertEqual(res.status_code, 200)
        renewal = res.json()["renewal"]
        self.assertIsNotNone(renewal)
        self.assertTrue(renewal["expired"])
        self.assertEqual(renewal["daysRemaining"], 0)


if __name__ == "__main__":
    unittest.main()
