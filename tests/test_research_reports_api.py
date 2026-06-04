# -*- coding: utf-8 -*-
"""Integration tests for paid research report APIs."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from src.config import Config
from src.storage import AppCreditLedger, AppResearchReport, AppUser, DatabaseManager
from src.users.passwords import hash_password
from src.users.sessions import issue_session


class ResearchReportApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "research_reports.db")
        self._saved_env = {
            key: os.environ.get(key)
            for key in [
                "DATABASE_PATH",
                "DATABASE_URL",
                "USER_PUBLIC_REGISTRATION_ENABLED",
                "USER_REQUIRE_EMAIL_VERIFICATION",
                "ADMIN_AUTH_ENABLED",
                "LITELLM_MODEL",
            ]
        }
        os.environ["DATABASE_PATH"] = self._db_path
        os.environ["DATABASE_URL"] = ""
        os.environ["USER_PUBLIC_REGISTRATION_ENABLED"] = "true"
        os.environ["USER_REQUIRE_EMAIL_VERIFICATION"] = "false"
        os.environ["ADMIN_AUTH_ENABLED"] = "false"
        os.environ["LITELLM_MODEL"] = "openai/gpt-4o-mini"

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db_manager = DatabaseManager.get_instance()

        from api.app import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._temp_dir.cleanup()

    def _create_user(
        self,
        email: str,
        *,
        is_admin: bool = False,
        is_research_operator: bool = False,
        credits: int = 0,
    ) -> AppUser:
        session = self.db_manager.get_session()
        try:
            user = AppUser(
                email=email,
                password_hash=hash_password("pw12345678"),
                is_admin=is_admin,
                is_research_operator=is_research_operator,
                credit_balance=credits,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user
        finally:
            session.close()

    def _login(self, user: AppUser) -> None:
        session = self.db_manager.get_session()
        try:
            issued = issue_session(session, user, ttl_hours=24)
            session.commit()
        finally:
            session.close()
        self.client.cookies.set("dsa_user_session", issued.cookie_value)

    def _logout_cookie(self) -> None:
        self.client.cookies.clear()

    def _create_published_report(self, operator: AppUser, price_credits: int = 30) -> int:
        self._login(operator)
        create_res = self.client.post("/api/v1/research-reports", json={
            "title": "半导体设备景气跟踪",
            "summary": "跟踪订单、国产替代与估值位置。",
            "previewContent": "试读：行业订单仍有韧性。",
            "fullContent": "全文：设备、材料和先进封装链条拆解。",
            "priceCredits": price_credits,
            "category": "行业",
            "tags": ["半导体", "设备"],
        })
        self.assertEqual(create_res.status_code, 201, create_res.text)
        report_id = create_res.json()["report"]["id"]
        publish_res = self.client.post(f"/api/v1/research-reports/{report_id}/publish")
        self.assertEqual(publish_res.status_code, 200, publish_res.text)
        self._logout_cookie()
        return int(report_id)

    def test_admin_publish_and_anonymous_can_only_read_preview(self) -> None:
        operator = self._create_user("operator@example.com", is_research_operator=True)
        report_id = self._create_published_report(operator)

        list_res = self.client.get("/api/v1/research-reports")
        self.assertEqual(list_res.status_code, 200, list_res.text)
        self.assertEqual(list_res.json()["count"], 1)

        detail_res = self.client.get(f"/api/v1/research-reports/{report_id}")
        self.assertEqual(detail_res.status_code, 200, detail_res.text)
        report = detail_res.json()["report"]
        self.assertFalse(report["isUnlocked"])
        self.assertEqual(report["previewContent"], "试读：行业订单仍有韧性。")
        self.assertIsNone(report["fullContent"])

    def test_user_can_purchase_with_credits_and_read_full_content(self) -> None:
        operator = self._create_user("operator@example.com", is_research_operator=True)
        report_id = self._create_published_report(operator, price_credits=30)
        user = self._create_user("user@example.com", credits=50)
        self._login(user)

        purchase_res = self.client.post(f"/api/v1/research-reports/{report_id}/purchase")
        self.assertEqual(purchase_res.status_code, 200, purchase_res.text)
        body = purchase_res.json()
        self.assertTrue(body["report"]["isUnlocked"])
        self.assertEqual(body["report"]["fullContent"], "全文：设备、材料和先进封装链条拆解。")
        self.assertEqual(body["creditBalance"], 20)

        session = self.db_manager.get_session()
        try:
            refreshed = session.query(AppUser).filter(AppUser.id == user.id).first()
            self.assertIsNotNone(refreshed)
            self.assertEqual(refreshed.credit_balance, 20)
            ledger = session.query(AppCreditLedger).filter(AppCreditLedger.user_id == user.id).first()
            self.assertIsNotNone(ledger)
            self.assertEqual(ledger.delta, -30)
        finally:
            session.close()

    def test_reaction_and_comments_are_recorded_per_user(self) -> None:
        operator = self._create_user("operator@example.com", is_research_operator=True)
        report_id = self._create_published_report(operator, price_credits=0)
        user = self._create_user("reader@example.com")
        self._login(user)

        reaction_res = self.client.post(
            f"/api/v1/research-reports/{report_id}/reaction",
            json={"reaction": "like"},
        )
        self.assertEqual(reaction_res.status_code, 200, reaction_res.text)
        self.assertEqual(reaction_res.json()["report"]["likes"], 1)
        self.assertEqual(reaction_res.json()["report"]["myReaction"], "like")

        comment_res = self.client.post(
            f"/api/v1/research-reports/{report_id}/comments",
            json={"content": "观点有参考价值"},
        )
        self.assertEqual(comment_res.status_code, 200, comment_res.text)

        comments_res = self.client.get(f"/api/v1/research-reports/{report_id}/comments")
        self.assertEqual(comments_res.status_code, 200, comments_res.text)
        self.assertEqual(comments_res.json()["count"], 1)
        self.assertEqual(comments_res.json()["comments"][0]["content"], "观点有参考价值")

    def test_operator_list_includes_own_drafts(self) -> None:
        operator = self._create_user("operator@example.com", is_research_operator=True)
        self._login(operator)
        session = self.db_manager.get_session()
        try:
            session.add(AppResearchReport(
                title="草稿研报",
                summary="草稿摘要",
                preview_content="草稿试读",
                full_content="草稿全文",
                price_credits=10,
                is_published=False,
                author_id=operator.id,
            ))
            session.commit()
        finally:
            session.close()

        res = self.client.get("/api/v1/research-reports/mine/list")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["count"], 1)
        self.assertFalse(res.json()["reports"][0]["isPublished"])

    def test_normal_user_cannot_create_research_report(self) -> None:
        user = self._create_user("normal@example.com")
        self._login(user)

        res = self.client.post("/api/v1/research-reports", json={
            "title": "普通用户草稿",
            "summary": "摘要",
            "previewContent": "试读",
            "fullContent": "全文",
            "priceCredits": 10,
        })

        self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
