# -*- coding: utf-8 -*-
"""Phase 2 商业化 endpoint 集成测试。

覆盖:
- ``/api/v1/billing/plans``: 返回数据库中的套餐目录。
- ``/api/v1/billing/subscription``: 未登录返回 401; 登录后返回当前 plan + 历史。
- ``/api/v1/account/redeem``: 兑换码合法 -> 升级到 Pro; 重复使用 -> 失败。
- ``/api/v1/account/model-preference``: 用户只能在套餐允许的平台模型中选择偏好。

测试用 SQLite + monkeypatched env 启用 To C 模式, 不依赖 LLM 运行时。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
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
    DatabaseManager,
)
from src.users.passwords import hash_password
from src.users.sessions import issue_session


def _enable_user_mode(monkeypatch_env: dict) -> None:
    monkeypatch_env["USER_PUBLIC_REGISTRATION_ENABLED"] = "true"
    monkeypatch_env["USER_REQUIRE_EMAIL_VERIFICATION"] = "false"


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
                "DATABASE_URL",
                "USER_PUBLIC_REGISTRATION_ENABLED",
                "USER_REQUIRE_EMAIL_VERIFICATION",
                "ADMIN_AUTH_ENABLED",
                "LITELLM_MODEL",
                "LITELLM_FALLBACK_MODELS",
                "AGENT_LITELLM_MODEL",
                "LITELLM_CONFIG",
                "LLM_CHANNELS",
            ]
        }
        os.environ["DATABASE_PATH"] = self._db_path
        os.environ["DATABASE_URL"] = ""
        os.environ["ADMIN_AUTH_ENABLED"] = "false"
        os.environ["LITELLM_MODEL"] = "openai/gpt-4o-mini"
        os.environ["LITELLM_FALLBACK_MODELS"] = "openai/gpt-4o"
        os.environ["AGENT_LITELLM_MODEL"] = ""
        os.environ["LITELLM_CONFIG"] = ""
        os.environ["LLM_CHANNELS"] = ""
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

    def _create_user(
        self,
        email: str = "u@example.com",
        *,
        plan_code: str = "free",
        is_admin: bool = False,
    ) -> AppUser:
        session = self.db_manager.get_session()
        try:
            user = AppUser(
                email=email,
                password_hash=hash_password("pw12345678"),
                plan_code=plan_code,
                is_admin=is_admin,
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

    def _seed_free_plan(
        self,
        *,
        daily_analysis_limit: int = 5,
        daily_agent_limit: int = 5,
        max_stocks: int = 3,
    ) -> AppPlan:
        session = self.db_manager.get_session()
        try:
            plan = session.query(AppPlan).filter(AppPlan.code == "free").first()
            if plan is None:
                plan = AppPlan(code="free")
            session.add(plan)
            plan.name = "免费会员"
            plan.daily_analysis_limit = daily_analysis_limit
            plan.daily_agent_limit = daily_agent_limit
            plan.max_stocks = max_stocks
            plan.can_webhook = False
            plan.price_cents = 0
            plan.currency = "CNY"
            plan.is_active = True
            session.commit()
            session.refresh(plan)
            return plan
        finally:
            session.close()

    def _seed_pro_plan(self) -> AppPlan:
        session = self.db_manager.get_session()
        try:
            plan = AppPlan(
                code="pro",
                name="Pro",
                daily_analysis_limit=50,
                daily_agent_limit=50,
                max_stocks=30,
                allowed_models='["openai/gpt-4o-mini","openai/gpt-4o"]',
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
    def test_plans_anonymous_returns_seeded_free_plan(self):
        res = self.client.get("/api/v1/billing/plans")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["userModeEnabled"])
        codes = {p["code"] for p in body["plans"]}
        self.assertEqual(codes, {"free"})
        self.assertIsNone(body["currentPlan"])

    def test_plans_uses_db_rows_when_present(self):
        self._seed_free_plan()
        self._seed_pro_plan()
        res = self.client.get("/api/v1/billing/plans")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        codes = {p["code"] for p in body["plans"]}
        self.assertIn("free", codes)
        self.assertIn("pro", codes)
        pro = next(p for p in body["plans"] if p["code"] == "pro")
        self.assertEqual(pro["dailyAnalysisLimit"], 50)
        self.assertTrue(pro["isPersisted"])

    def test_plans_free_db_row_overrides_free_limits(self):
        self._seed_free_plan(daily_analysis_limit=8, daily_agent_limit=6, max_stocks=4)

        res = self.client.get("/api/v1/billing/plans")
        self.assertEqual(res.status_code, 200)
        free = next(p for p in res.json()["plans"] if p["code"] == "free")
        self.assertEqual(free["dailyAnalysisLimit"], 8)
        self.assertEqual(free["dailyAgentLimit"], 6)
        self.assertEqual(free["maxStocks"], 4)
        self.assertTrue(free["isPersisted"])

    def test_plans_includes_current_plan_when_logged_in(self):
        self._seed_free_plan()
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
        self._seed_free_plan()
        user = self._create_user()
        self._login(user)
        res = self.client.get("/api/v1/billing/subscription")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["plan"]["code"], "free")
        self.assertFalse(body["plan"]["isActivePaid"])
        self.assertEqual(body["subscriptions"], [])


class TestAdminPlanConfig(_BaseApi):
    def test_admin_can_update_free_plan_limits(self):
        admin = self._create_user(email="admin@example.com", is_admin=True)
        self._login(admin)

        res = self.client.put("/api/v1/admin/plans/free", json={
            "name": "免费会员",
            "dailyAnalysisLimit": 8,
            "dailyAgentLimit": 6,
            "maxStocks": 4,
            "canWebhook": True,
            "priceCents": 999,
            "currency": "CNY",
            "isActive": False,
        })
        self.assertEqual(res.status_code, 200)
        plan = res.json()["plan"]
        self.assertEqual(plan["code"], "free")
        self.assertEqual(plan["dailyAnalysisLimit"], 8)
        self.assertEqual(plan["dailyAgentLimit"], 6)
        self.assertEqual(plan["maxStocks"], 4)
        self.assertFalse(plan["canWebhook"])
        self.assertEqual(plan["priceCents"], 0)
        self.assertTrue(plan["isActive"])

        plans_res = self.client.get("/api/v1/billing/plans")
        self.assertEqual(plans_res.status_code, 200)
        free = next(p for p in plans_res.json()["plans"] if p["code"] == "free")
        self.assertEqual(free["dailyAnalysisLimit"], 8)
        self.assertEqual(free["dailyAgentLimit"], 6)
        self.assertEqual(free["maxStocks"], 4)


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
        self._seed_free_plan()
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
        self.assertNotIn("can" + "B" + "yok", body["plan"])

        # 再次兑换同一个 code 应失败
        res2 = self.client.post("/api/v1/account/redeem", json={"code": "VIP-AAAA"})
        self.assertEqual(res2.status_code, 400)
        self.assertEqual(res2.json()["error"], "invite_code_invalid")


class TestModelPreferenceEndpoint(_BaseApi):
    def test_list_requires_login(self):
        res = self.client.get("/api/v1/account/model-preference")
        self.assertEqual(res.status_code, 401)

    def test_free_user_can_read_platform_models(self):
        self._seed_free_plan()
        user = self._create_user()
        self._login(user)
        res = self.client.get("/api/v1/account/model-preference")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["models"], ["openai/gpt-4o-mini", "openai/gpt-4o"])

    def test_user_can_update_allowed_model_preference(self):
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

        res = self.client.patch(
            "/api/v1/account/model-preference",
            json={"preferredModel": "openai/gpt-4o"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["preferredModel"], "openai/gpt-4o")

    def test_rejects_disallowed_model_preference(self):
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
        res = self.client.patch(
            "/api/v1/account/model-preference",
            json={"preferredModel": "anthropic/claude-3-5-sonnet"},
        )
        self.assertEqual(res.status_code, 400)


class TestAccountStatusPlan(_BaseApi):
    def test_status_includes_plan_block_when_logged_in(self):
        self._seed_free_plan()
        user = self._create_user()
        self._login(user)
        res = self.client.get("/api/v1/account/status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["loggedIn"])
        self.assertNotIn("limits", body)
        self.assertIsNotNone(body["plan"])
        self.assertEqual(body["plan"]["code"], "free")
        self.assertFalse(body["plan"]["isPro"])

    def test_status_anonymous_has_no_plan(self):
        res = self.client.get("/api/v1/account/status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["loggedIn"])
        self.assertNotIn("limits", body)
        self.assertIsNone(body["plan"])
        self.assertIsNone(body["renewal"])

    def test_status_renewal_null_for_free_user(self):
        self._seed_free_plan()
        user = self._create_user()
        self._login(user)
        res = self.client.get("/api/v1/account/status")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        # free 档无续费提示
        self.assertIsNone(body["renewal"])

    def test_status_renewal_flags_expiring_soon(self):
        self._seed_free_plan()
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
        self._seed_free_plan()
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
