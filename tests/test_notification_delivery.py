# -*- coding: utf-8 -*-
"""``src/users/notification_delivery`` 单元测试。

覆盖:
- HTML 邮件 body 含一键退订链接 + 免责声明
- ``send_daily_email`` 使用注入的 backend, 成功/失败均不抛
- ``dispatch_user_webhook`` 按 webhook_type 分发到不同 schema
- 免费档 / 未配置 webhook 时静默 skip
"""

from __future__ import annotations

import os
import sys
import unittest
from typing import Any, Dict, List
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.users.email import EmailMessageDTO
from src.users.notification_prefs import NotificationPrefs
from src.users import notification_delivery as nd


class _RecordingBackend:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: List[EmailMessageDTO] = []

    def send(self, message: EmailMessageDTO) -> None:
        if self.fail:
            raise RuntimeError("backend failure")
        self.sent.append(message)


class EmailBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["UNSUBSCRIBE_SIGNING_KEY"] = "unit-test-signing-key"
        os.environ["USER_PUBLIC_BASE_URL"] = "https://dsa.example.com"

    def tearDown(self) -> None:
        os.environ.pop("UNSUBSCRIBE_SIGNING_KEY", None)
        os.environ.pop("USER_PUBLIC_BASE_URL", None)

    def test_build_message_has_html_and_unsubscribe_link(self) -> None:
        ctx = nd.DailyEmailContext(
            user_id=42,
            user_email="user@example.com",
            subject="[DSA] daily test",
            report_markdown="# Hello\n\n- bullet 1\n- bullet 2",
        )
        msg = nd.build_daily_email_message(ctx)
        self.assertEqual(msg.to, "user@example.com")
        self.assertEqual(msg.subject, "[DSA] daily test")
        self.assertIsNotNone(msg.body_html)
        assert msg.body_html is not None
        self.assertIn("https://dsa.example.com/api/v1/account/notification-prefs/unsubscribe?token=", msg.body_html)
        self.assertIn("一键退订", msg.body_html)
        self.assertIn("不构成投资建议", msg.body_html)
        # text 版同样含退订 URL
        self.assertIn("https://dsa.example.com/api/v1/account/notification-prefs/unsubscribe?token=", msg.body_text)

    def test_send_daily_email_uses_backend(self) -> None:
        backend = _RecordingBackend()
        ctx = nd.DailyEmailContext(
            user_id=1,
            user_email="a@b.com",
            subject="s",
            report_markdown="hello",
        )
        ok = nd.send_daily_email(ctx, backend=backend)
        self.assertTrue(ok)
        self.assertEqual(len(backend.sent), 1)
        self.assertEqual(backend.sent[0].to, "a@b.com")

    def test_send_daily_email_swallows_backend_error(self) -> None:
        backend = _RecordingBackend(fail=True)
        ctx = nd.DailyEmailContext(
            user_id=1, user_email="a@b.com", subject="s", report_markdown="hello"
        )
        ok = nd.send_daily_email(ctx, backend=backend)
        self.assertFalse(ok)

    def test_send_daily_email_skips_when_no_email(self) -> None:
        backend = _RecordingBackend()
        ctx = nd.DailyEmailContext(
            user_id=1, user_email="", subject="s", report_markdown="hello"
        )
        ok = nd.send_daily_email(ctx, backend=backend)
        self.assertFalse(ok)
        self.assertEqual(backend.sent, [])


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


class WebhookDispatchTests(unittest.TestCase):
    def _make_prefs(self, *, webhook_type: str | None, url: str | None = "https://hook/x") -> NotificationPrefs:
        return NotificationPrefs(
            daily_push_enabled=True,
            email_enabled=True,
            webhook_url=url,
            webhook_type=webhook_type,
        )

    def test_can_webhook_false_short_circuits(self) -> None:
        prefs = self._make_prefs(webhook_type="feishu")
        with mock.patch.object(nd, "requests") as fake_requests:
            ok = nd.dispatch_user_webhook(prefs, can_webhook=False, content="hi")
            self.assertFalse(ok)
            fake_requests.post.assert_not_called()

    def test_missing_webhook_url_skipped(self) -> None:
        prefs = self._make_prefs(webhook_type="feishu", url=None)
        with mock.patch.object(nd, "requests") as fake_requests:
            ok = nd.dispatch_user_webhook(prefs, can_webhook=True, content="hi")
            self.assertFalse(ok)
            fake_requests.post.assert_not_called()

    def test_unknown_webhook_type_skipped(self) -> None:
        prefs = self._make_prefs(webhook_type="myspace")
        with mock.patch.object(nd, "requests") as fake_requests:
            ok = nd.dispatch_user_webhook(prefs, can_webhook=True, content="hi")
            self.assertFalse(ok)
            fake_requests.post.assert_not_called()

    def test_feishu_card_payload(self) -> None:
        prefs = self._make_prefs(webhook_type="feishu")
        calls: List[Dict[str, Any]] = []

        def _fake_post(url: str, json: Dict[str, Any], timeout: int = 0) -> _FakeResponse:
            calls.append({"url": url, "json": json})
            return _FakeResponse(200)

        with mock.patch.object(nd.requests, "post", side_effect=_fake_post):
            ok = nd.dispatch_user_webhook(
                prefs, can_webhook=True, content="hello", title="DSA 报告"
            )
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        body = calls[0]["json"]
        self.assertEqual(body["msg_type"], "interactive")
        self.assertEqual(
            body["card"]["elements"][0]["text"]["tag"], "lark_md"
        )
        self.assertIn("hello", body["card"]["elements"][0]["text"]["content"])

    def test_feishu_card_fallback_to_text(self) -> None:
        prefs = self._make_prefs(webhook_type="feishu")
        calls: List[Dict[str, Any]] = []

        def _fake_post(url: str, json: Dict[str, Any], timeout: int = 0) -> _FakeResponse:
            calls.append(json)
            # 第一次失败, 第二次成功 -> 触发回退
            return _FakeResponse(500) if len(calls) == 1 else _FakeResponse(200)

        with mock.patch.object(nd.requests, "post", side_effect=_fake_post):
            ok = nd.dispatch_user_webhook(prefs, can_webhook=True, content="hi")
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["msg_type"], "interactive")
        self.assertEqual(calls[1]["msg_type"], "text")

    def test_wecom_markdown_payload(self) -> None:
        prefs = self._make_prefs(webhook_type="wecom")
        seen: Dict[str, Any] = {}

        def _fake_post(url: str, json: Dict[str, Any], timeout: int = 0) -> _FakeResponse:
            seen.update(json)
            return _FakeResponse(200)

        with mock.patch.object(nd.requests, "post", side_effect=_fake_post):
            ok = nd.dispatch_user_webhook(
                prefs, can_webhook=True, content="line1\nline2", title="T"
            )
        self.assertTrue(ok)
        self.assertEqual(seen["msgtype"], "markdown")
        self.assertIn("line1", seen["markdown"]["content"])

    def test_discord_content_payload(self) -> None:
        prefs = self._make_prefs(webhook_type="discord")
        seen: Dict[str, Any] = {}

        def _fake_post(url: str, json: Dict[str, Any], timeout: int = 0) -> _FakeResponse:
            seen.update(json)
            return _FakeResponse(200)

        with mock.patch.object(nd.requests, "post", side_effect=_fake_post):
            ok = nd.dispatch_user_webhook(
                prefs, can_webhook=True, content="message body", title="T"
            )
        self.assertTrue(ok)
        self.assertIn("message body", seen["content"])

    def test_telegram_text_payload(self) -> None:
        prefs = self._make_prefs(webhook_type="telegram")
        seen: Dict[str, Any] = {}

        def _fake_post(url: str, json: Dict[str, Any], timeout: int = 0) -> _FakeResponse:
            seen.update(json)
            return _FakeResponse(200)

        with mock.patch.object(nd.requests, "post", side_effect=_fake_post):
            ok = nd.dispatch_user_webhook(
                prefs, can_webhook=True, content="message body", title="T"
            )
        self.assertTrue(ok)
        self.assertIn("message body", seen["text"])
        self.assertEqual(seen["parse_mode"], "Markdown")

    def test_generic_payload_shape(self) -> None:
        prefs = self._make_prefs(webhook_type="generic")
        seen: Dict[str, Any] = {}

        def _fake_post(url: str, json: Dict[str, Any], timeout: int = 0) -> _FakeResponse:
            seen.update(json)
            return _FakeResponse(200)

        with mock.patch.object(nd.requests, "post", side_effect=_fake_post):
            ok = nd.dispatch_user_webhook(
                prefs, can_webhook=True, content="hi", title="T"
            )
        self.assertTrue(ok)
        self.assertEqual(seen["title"], "T")
        self.assertEqual(seen["content"], "hi")

    def test_request_exception_returns_false(self) -> None:
        prefs = self._make_prefs(webhook_type="wecom")
        with mock.patch.object(
            nd.requests,
            "post",
            side_effect=nd.requests.RequestException("boom"),
        ):
            ok = nd.dispatch_user_webhook(prefs, can_webhook=True, content="hi")
        self.assertFalse(ok)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
