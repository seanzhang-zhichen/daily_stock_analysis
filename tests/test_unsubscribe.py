# -*- coding: utf-8 -*-
"""``src/users/unsubscribe`` 单元测试。

覆盖:
- 合法 token 可签发 + 校验通过
- tamper / 错签名 / 过期 / 不合法 action 均失败 (返回 None)
- ``build_unsubscribe_url`` 拼接默认走 ``USER_PUBLIC_BASE_URL`` 环境变量
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.users import unsubscribe as us


class TokenRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["UNSUBSCRIBE_SIGNING_KEY"] = "unit-test-signing-key"

    def tearDown(self) -> None:
        os.environ.pop("UNSUBSCRIBE_SIGNING_KEY", None)
        os.environ.pop("USER_PUBLIC_BASE_URL", None)

    def test_token_round_trip_daily(self) -> None:
        token = us.build_unsubscribe_token(user_id=42, action=us.ACTION_DAILY)
        claim = us.verify_unsubscribe_token(token)
        self.assertIsNotNone(claim)
        assert claim is not None  # for mypy / readability
        self.assertEqual(claim.user_id, 42)
        self.assertEqual(claim.action, us.ACTION_DAILY)

    def test_token_round_trip_email(self) -> None:
        token = us.build_unsubscribe_token(user_id=7, action=us.ACTION_EMAIL)
        claim = us.verify_unsubscribe_token(token)
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertEqual(claim.user_id, 7)
        self.assertEqual(claim.action, us.ACTION_EMAIL)

    def test_invalid_action_rejected_on_build(self) -> None:
        with self.assertRaises(ValueError):
            us.build_unsubscribe_token(user_id=1, action="nope")

    def test_tampered_token_rejected(self) -> None:
        token = us.build_unsubscribe_token(user_id=42, action=us.ACTION_DAILY)
        # 篡改最后一个字符 (签名段)
        if token.endswith("A"):
            bad = token[:-1] + "B"
        else:
            bad = token[:-1] + "A"
        self.assertIsNone(us.verify_unsubscribe_token(bad))

    def test_wrong_signing_key_rejected(self) -> None:
        token = us.build_unsubscribe_token(user_id=42, action=us.ACTION_DAILY)
        os.environ["UNSUBSCRIBE_SIGNING_KEY"] = "another-key"
        self.assertIsNone(us.verify_unsubscribe_token(token))

    def test_expired_token_rejected(self) -> None:
        # 强制 issued_at 远早于现在, 触发 TTL 过期
        token = us.build_unsubscribe_token(
            user_id=42, action=us.ACTION_DAILY, issued_at=0
        )
        self.assertIsNone(us.verify_unsubscribe_token(token, ttl_seconds=10))

    def test_malformed_token_rejected(self) -> None:
        self.assertIsNone(us.verify_unsubscribe_token(""))
        self.assertIsNone(us.verify_unsubscribe_token("no-dot"))
        self.assertIsNone(us.verify_unsubscribe_token("abc.def.ghi"))

    def test_build_unsubscribe_url_uses_env_base(self) -> None:
        os.environ["USER_PUBLIC_BASE_URL"] = "https://dsa.example.com/"
        url = us.build_unsubscribe_url(user_id=10, action=us.ACTION_DAILY)
        self.assertTrue(
            url.startswith("https://dsa.example.com/api/v1/account/notification-prefs/unsubscribe?token=")
        )
        # token 不应被截断
        token = url.split("token=", 1)[1]
        claim = us.verify_unsubscribe_token(token)
        self.assertIsNotNone(claim)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
