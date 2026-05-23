# -*- coding: utf-8 -*-
"""Phase 5 真实支付通道接入回归测试。

覆盖:

- :class:`WechatGateway.verify_callback`: 签名通过 / 签名篡改 / 时间戳偏差。
- :class:`AlipayGateway.verify_callback`: 签名通过 / app_id 不一致 / 篡改。
- :meth:`OrderService.process_callback`: 驱动 ``fulfill_order`` + 金额不一致
  阻断 + 幂等去重 + signature_invalid 不驱动。
- :meth:`OrderService.approve_refund`: 显式传入 ``provider_refund_no`` 跳过
  gateway; 不传时通过 :func:`set_gateway_override` 注入 mock gateway 拿到通道
  退款单号; gateway 抛 NotImplementedError 时回退人工模式。
- :meth:`OrderService.issue_invoice`: 发送邮件回执 (通过捕获邮件 backend 验证)。

不依赖外网 / 真实通道证书; 用 ``cryptography`` 即时生成测试 RSA 密钥对。
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
import zipfile
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from unittest.mock import MagicMock

# Stub optional litellm dep before any src.* import.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.services.billing.gateways.alipay import AlipayGateway
from src.services.billing.gateways.base import CallbackResult
from src.services.billing.gateways.factory import (
    clear_gateway_overrides,
    get_gateway,
    set_gateway_override,
)
from src.services.billing.gateways.wechat import WechatGateway, _parse_wechat_bill_csv
from src.services.billing.gateways.alipay import _parse_alipay_bill_csv


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: build signed wechat / alipay callback payloads on the fly.
# ─────────────────────────────────────────────────────────────────────────────


def _gen_rsa_keypair() -> Tuple[rsa.RSAPrivateKey, str]:
    """生成测试 RSA 密钥对, 返回 (private_key, public_pem_str)。"""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return priv, pub_pem


def _sign_pkcs1_sha256(priv: rsa.RSAPrivateKey, message: bytes) -> str:
    sig = priv.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode("ascii")


def _build_wechat_callback(
    priv: rsa.RSAPrivateKey,
    apiv3_key: bytes,
    *,
    out_trade_no: str = "DSA20260101AAAAAAAAAA",
    transaction_id: str = "4200001234567890",
    amount_total: int = 3900,
    trade_state: str = "SUCCESS",
    timestamp_offset: int = 0,
    tamper_body: bool = False,
) -> Tuple[dict, bytes]:
    """构建一份合法的微信 V3 回调 (headers, body)。"""
    ts = str(int(time.time()) + timestamp_offset)
    nonce = "test-nonce-1234"

    # 1) 准备业务字段并 AES-256-GCM 加密
    business = {
        "mchid": "1230000109",
        "appid": "wxd678efh567hg6787",
        "out_trade_no": out_trade_no,
        "transaction_id": transaction_id,
        "trade_state": trade_state,
        "amount": {"total": amount_total, "payer_total": amount_total, "currency": "CNY"},
    }
    plaintext = json.dumps(business).encode("utf-8")
    resource_nonce = "abc123def456"  # 12 bytes
    associated_data = "transaction"

    aes = AESGCM(apiv3_key)
    ciphertext = aes.encrypt(resource_nonce.encode("utf-8"), plaintext, associated_data.encode("utf-8"))

    payload = {
        "id": "EV-202601011000-001",
        "create_time": "2026-01-01T10:00:00+08:00",
        "event_type": "TRANSACTION.SUCCESS",
        "summary": "支付成功",
        "resource": {
            "original_type": "transaction",
            "algorithm": "AEAD_AES_256_GCM",
            "associated_data": associated_data,
            "nonce": resource_nonce,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        },
    }
    body = json.dumps(payload).encode("utf-8")
    if tamper_body:
        body = body[:-1] + b" "  # 在合法 JSON 后追加空格, 破坏签名串

    # 2) 用 priv 对未篡改的签名串签名 (篡改路径会让验签必败)
    signed_str = f"{ts}\n{nonce}\n{json.dumps(payload)}\n".encode("utf-8")
    sig_b64 = _sign_pkcs1_sha256(priv, signed_str)

    headers = {
        "Wechatpay-Timestamp": ts,
        "Wechatpay-Nonce": nonce,
        "Wechatpay-Signature": sig_b64,
        "Wechatpay-Serial": "test-cert-001",
    }
    return headers, body


def _build_alipay_callback(
    priv: rsa.RSAPrivateKey,
    *,
    app_id: str = "2021000000000000",
    out_trade_no: str = "DSA20260101BBBBBBBBBB",
    trade_no: str = "20260101220000001",
    total_amount: str = "39.00",
    trade_status: str = "TRADE_SUCCESS",
    tamper: bool = False,
) -> bytes:
    """构建一份合法的支付宝异步通知 (form-urlencoded body)。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    params = {
        "app_id": app_id,
        "notify_id": "NID-202601011200-001",
        "notify_time": now,
        "notify_type": "trade_status_sync",
        "out_trade_no": out_trade_no,
        "trade_no": trade_no,
        "total_amount": total_amount,
        "trade_status": trade_status,
        "charset": "utf-8",
        "version": "1.0",
    }
    # alipay 签名串: 按 key ASCII 升序, 跳过空值
    sorted_items = sorted(params.items(), key=lambda kv: kv[0])
    signed_str = "&".join(f"{k}={v}" for k, v in sorted_items if v != "").encode("utf-8")
    sig_b64 = _sign_pkcs1_sha256(priv, signed_str)
    params["sign"] = sig_b64
    params["sign_type"] = "RSA2"

    if tamper:
        params["total_amount"] = "9999.00"  # 篡改后签名必失败

    import urllib.parse
    return urllib.parse.urlencode(params).encode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# WechatGateway.verify_callback
# ─────────────────────────────────────────────────────────────────────────────


class TestWechatGatewayVerify(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.priv, cls.pub_pem = _gen_rsa_keypair()
        cls.apiv3_key = b"0123456789abcdef0123456789abcdef"  # 32 字节
        cls.gateway = WechatGateway(
            app_id="wxd678efh567hg6787",
            mch_id="1230000109",
            apiv3_key=cls.apiv3_key,
            platform_cert_pem=cls.pub_pem,
        )

    def test_valid_callback_decrypts_to_paid(self):
        headers, body = _build_wechat_callback(self.priv, self.apiv3_key)
        result = self.gateway.verify_callback(headers, body)
        self.assertTrue(result.signature_valid)
        self.assertFalse(result.parse_error)
        self.assertEqual(result.status, "paid")
        self.assertEqual(result.event_type, "pay.success")
        self.assertEqual(result.out_trade_no, "DSA20260101AAAAAAAAAA")
        self.assertEqual(result.provider_trade_no, "4200001234567890")
        self.assertEqual(result.amount_cents, 3900)
        self.assertEqual(result.event_id, "EV-202601011000-001")

    def test_tampered_body_fails_signature(self):
        headers, body = _build_wechat_callback(
            self.priv, self.apiv3_key, tamper_body=True
        )
        result = self.gateway.verify_callback(headers, body)
        self.assertFalse(result.signature_valid)
        self.assertEqual(result.status, "unknown")

    def test_timestamp_skew_rejected(self):
        headers, body = _build_wechat_callback(
            self.priv, self.apiv3_key, timestamp_offset=-7200
        )
        result = self.gateway.verify_callback(headers, body)
        self.assertFalse(result.signature_valid)

    def test_missing_signature_header_rejected(self):
        headers, body = _build_wechat_callback(self.priv, self.apiv3_key)
        headers.pop("Wechatpay-Signature")
        result = self.gateway.verify_callback(headers, body)
        self.assertFalse(result.signature_valid)

    def test_closed_trade_state_mapped(self):
        headers, body = _build_wechat_callback(
            self.priv, self.apiv3_key, trade_state="CLOSED"
        )
        result = self.gateway.verify_callback(headers, body)
        self.assertTrue(result.signature_valid)
        self.assertEqual(result.status, "closed")
        self.assertEqual(result.event_type, "pay.fail")


# ─────────────────────────────────────────────────────────────────────────────
# AlipayGateway.verify_callback
# ─────────────────────────────────────────────────────────────────────────────


class TestAlipayGatewayVerify(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.priv, cls.pub_pem = _gen_rsa_keypair()
        cls.gateway = AlipayGateway(
            app_id="2021000000000000",
            alipay_public_key_pem=cls.pub_pem,
        )

    def test_valid_notify_marks_paid(self):
        body = _build_alipay_callback(self.priv)
        result = self.gateway.verify_callback({}, body)
        self.assertTrue(result.signature_valid)
        self.assertEqual(result.status, "paid")
        self.assertEqual(result.event_type, "pay.success")
        self.assertEqual(result.out_trade_no, "DSA20260101BBBBBBBBBB")
        self.assertEqual(result.provider_trade_no, "20260101220000001")
        self.assertEqual(result.amount_cents, 3900)
        self.assertEqual(result.event_id, "NID-202601011200-001")

    def test_app_id_mismatch_rejected(self):
        body = _build_alipay_callback(self.priv, app_id="9999999999999999")
        result = self.gateway.verify_callback({}, body)
        self.assertFalse(result.signature_valid)

    def test_tampered_amount_fails_signature(self):
        body = _build_alipay_callback(self.priv, tamper=True)
        result = self.gateway.verify_callback({}, body)
        self.assertFalse(result.signature_valid)

    def test_trade_closed_status(self):
        body = _build_alipay_callback(self.priv, trade_status="TRADE_CLOSED")
        result = self.gateway.verify_callback({}, body)
        self.assertTrue(result.signature_valid)
        self.assertEqual(result.status, "closed")


# ─────────────────────────────────────────────────────────────────────────────
# OrderService.process_callback / approve_refund / issue_invoice
# 需要真实 SQLite, 用 _BillingSvcBase 起一份。
# ─────────────────────────────────────────────────────────────────────────────


class _MockGateway:
    """单测用的最小 gateway 替身; 仅实现 refund / fetch_settlements。"""

    def __init__(self, provider: str, refund_no: str = "WX_REFUND_FAKE"):
        self.provider = provider
        self._refund_no = refund_no
        self.refund_calls: List[dict] = []
        self.raise_not_implemented = False

    def refund(self, out_trade_no, out_refund_no, amount_cents, total_cents, reason=None):
        self.refund_calls.append({
            "out_trade_no": out_trade_no,
            "out_refund_no": out_refund_no,
            "amount_cents": amount_cents,
            "total_cents": total_cents,
            "reason": reason,
        })
        if self.raise_not_implemented:
            raise NotImplementedError
        return self._refund_no

    def fetch_settlements(self, target_date):
        return []

    def verify_callback(self, headers, body):  # pragma: no cover - 兼容接口
        raise NotImplementedError


class _BillingSvcBase(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["PAYMENT_ENABLED"] = "true"  # 让 factory 进入 build 分支(测试 override 还是优先)
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "billing.db")
        self._saved_env = {k: os.environ.get(k) for k in ["DATABASE_PATH", "USER_EMAIL_BACKEND"]}
        os.environ["DATABASE_PATH"] = self._db_path
        # 邮件后端: 默认 logging, 这里再保险设一下
        os.environ.pop("USER_EMAIL_BACKEND", None)

        from src.config import Config
        from src.storage import DatabaseManager
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db_manager = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        clear_gateway_overrides()
        from src.storage import DatabaseManager
        DatabaseManager.reset_instance()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.environ.pop("PAYMENT_ENABLED", None)
        self._temp_dir.cleanup()

    # ── 工具方法 ───────────────────────────────────────────────────────────

    def _seed_pro_plan(self) -> None:
        from src.storage import AppPlan
        session = self.db_manager.get_session()
        try:
            session.add(AppPlan(
                code="pro", name="Pro", daily_analysis_limit=50,
                daily_agent_limit=50, max_stocks=30,
                can_webhook=True, price_cents=3900,
            ))
            session.commit()
        finally:
            session.close()

    def _create_user(self, email: str = "u@example.com"):
        from src.storage import AppUser
        from src.users.passwords import hash_password
        session = self.db_manager.get_session()
        try:
            user = AppUser(
                email=email,
                password_hash=hash_password("pw12345678"),
                plan_code="free",
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user
        finally:
            session.close()

    def _create_order(self, user, *, amount_cents: int = 3900, provider: str = "wechat"):
        from src.services.billing import OrderService
        svc = OrderService()
        session = self.db_manager.get_session()
        try:
            return svc.create_order(
                db=session,
                user=user,
                plan_code="pro",
                provider=provider,
            )
        finally:
            session.close()


# ── process_callback ────────────────────────────────────────────────────────


class TestProcessCallback(_BillingSvcBase):
    def test_paid_callback_fulfills_and_is_idempotent(self):
        from src.services.billing import OrderService
        from src.storage import AppOrder, AppPaymentEvent

        self._seed_pro_plan()
        user = self._create_user()
        order = self._create_order(user)

        svc = OrderService()
        session = self.db_manager.get_session()
        try:
            # 测试需要 order 状态从 created 直接到 pending->paid;
            # process_callback 内部 fulfill_order 要求 pending 状态
            order = svc.get_order(session, order.order_no)
            svc.mark_pending(session, order)

            result = CallbackResult(
                provider="wechat",
                signature_valid=True,
                event_id="EV-PAID-001",
                event_type="pay.success",
                out_trade_no=order.order_no,
                provider_trade_no="WX_TXN_001",
                amount_cents=order.amount_cents,
                status="paid",
                raw_payload="{...}",
            )
            outcome = svc.process_callback(session, result, signature_raw="sig=abc")
            self.assertTrue(outcome.fulfilled)
            self.assertFalse(outcome.already_processed)

            # 订单变成 paid + 用户升级到 pro
            order = svc.get_order(session, order.order_no)
            self.assertEqual(order.status, "paid")
            self.assertEqual(order.provider_trade_no, "WX_TXN_001")

            # 幂等: 同一 event_id 再来一次, 不再 fulfill
            outcome2 = svc.process_callback(session, result, signature_raw="sig=abc")
            self.assertFalse(outcome2.fulfilled)
            self.assertTrue(outcome2.already_processed)

            # AppPaymentEvent 只落一条
            count = session.query(AppPaymentEvent).filter(
                AppPaymentEvent.provider_event_id == "EV-PAID-001"
            ).count()
            self.assertEqual(count, 1)
        finally:
            session.close()

    def test_invalid_signature_does_not_fulfill(self):
        from src.services.billing import OrderService

        self._seed_pro_plan()
        user = self._create_user()
        order = self._create_order(user)

        svc = OrderService()
        session = self.db_manager.get_session()
        try:
            order = svc.get_order(session, order.order_no)
            svc.mark_pending(session, order)

            result = CallbackResult(
                provider="wechat",
                signature_valid=False,
                event_id="EV-BAD-SIG",
                out_trade_no=order.order_no,
                amount_cents=order.amount_cents,
                status="paid",
            )
            outcome = svc.process_callback(session, result)
            self.assertFalse(outcome.fulfilled)
            self.assertEqual(outcome.reason, "signature_invalid")

            order = svc.get_order(session, order.order_no)
            self.assertEqual(order.status, "pending")  # 未 fulfill
        finally:
            session.close()

    def test_amount_mismatch_blocks_fulfill(self):
        from src.services.billing import OrderService

        self._seed_pro_plan()
        user = self._create_user()
        order = self._create_order(user)

        svc = OrderService()
        session = self.db_manager.get_session()
        try:
            order = svc.get_order(session, order.order_no)
            svc.mark_pending(session, order)

            result = CallbackResult(
                provider="wechat",
                signature_valid=True,
                event_id="EV-AMT-001",
                out_trade_no=order.order_no,
                amount_cents=order.amount_cents + 100,  # 故意不一致
                status="paid",
            )
            outcome = svc.process_callback(session, result)
            self.assertFalse(outcome.fulfilled)
            self.assertEqual(outcome.reason, "amount_mismatch")

            order = svc.get_order(session, order.order_no)
            self.assertEqual(order.status, "pending")
        finally:
            session.close()


# ── approve_refund 调用 gateway ─────────────────────────────────────────────


class TestApproveRefundGateway(_BillingSvcBase):
    def _setup_paid_order(self):
        from src.services.billing import OrderService
        self._seed_pro_plan()
        user = self._create_user()
        admin = self._create_user(email="admin@example.com")
        order = self._create_order(user)

        svc = OrderService()
        session = self.db_manager.get_session()
        try:
            order = svc.get_order(session, order.order_no)
            svc.mark_pending(session, order)
            svc.fulfill_order(session, order, provider_trade_no="WX_TXN_PAID")
            refund = svc.create_refund(
                db=session, order=order, user=user,
                amount_cents=order.amount_cents, reason="测试退款",
            )
            return svc, refund, admin, user
        finally:
            session.close()

    def test_explicit_provider_refund_no_skips_gateway(self):
        mock_gw = _MockGateway("wechat")
        set_gateway_override("wechat", mock_gw)

        svc, refund, admin, _ = self._setup_paid_order()
        session = self.db_manager.get_session()
        try:
            refund_db = svc.get_refund(session, refund.refund_no)
            admin_db = session.merge(admin)
            refund_db = svc.approve_refund(
                session, refund_db, reviewer=admin_db,
                provider_refund_no="MANUAL_REFUND_001",
            )
            self.assertEqual(refund_db.provider_refund_no, "MANUAL_REFUND_001")
            self.assertEqual(mock_gw.refund_calls, [])  # gateway 没被调用
        finally:
            session.close()

    def test_no_provider_refund_no_invokes_gateway(self):
        mock_gw = _MockGateway("wechat", refund_no="WX_REFUND_REAL_001")
        set_gateway_override("wechat", mock_gw)

        svc, refund, admin, _ = self._setup_paid_order()
        session = self.db_manager.get_session()
        try:
            refund_db = svc.get_refund(session, refund.refund_no)
            admin_db = session.merge(admin)
            refund_db = svc.approve_refund(session, refund_db, reviewer=admin_db)
            self.assertEqual(refund_db.provider_refund_no, "WX_REFUND_REAL_001")
            self.assertEqual(len(mock_gw.refund_calls), 1)
            self.assertEqual(mock_gw.refund_calls[0]["out_trade_no"], refund.order_no)
        finally:
            session.close()

    def test_gateway_not_implemented_falls_back_to_manual(self):
        mock_gw = _MockGateway("wechat")
        mock_gw.raise_not_implemented = True
        set_gateway_override("wechat", mock_gw)

        svc, refund, admin, _ = self._setup_paid_order()
        session = self.db_manager.get_session()
        try:
            refund_db = svc.get_refund(session, refund.refund_no)
            admin_db = session.merge(admin)
            refund_db = svc.approve_refund(session, refund_db, reviewer=admin_db)
            # gateway 抛 NotImplementedError 时, provider_refund_no 应保持为空
            self.assertIsNone(refund_db.provider_refund_no)
            # 退款状态仍是 refunded (审核流程不被通道失败阻断)
            self.assertEqual(refund_db.status, "refunded")
        finally:
            session.close()


# ── issue_invoice 邮件回执 ──────────────────────────────────────────────────


class TestIssueInvoiceEmail(_BillingSvcBase):
    def test_issue_invoice_sends_receipt(self):
        from src.services.billing import OrderService
        from src.services.billing import order_service as svc_module
        from src.users.email import EmailMessageDTO

        self._seed_pro_plan()
        user = self._create_user(email="buyer@example.com")
        admin = self._create_user(email="admin@example.com")
        order = self._create_order(user)

        svc = OrderService()
        captured: List[EmailMessageDTO] = []

        class _Capture:
            def send(self, message):
                captured.append(message)

        session = self.db_manager.get_session()
        try:
            order = svc.get_order(session, order.order_no)
            svc.mark_pending(session, order)
            svc.fulfill_order(session, order)

            invoice = svc.create_invoice(
                db=session, order=order, user=user,
                invoice_type="personal", title="测试个人发票",
                email="buyer@example.com",
            )

            # 注入捕获 backend (通过 monkey-patch get_email_backend)
            import src.users.email as email_mod
            original = email_mod.get_email_backend
            email_mod.get_email_backend = lambda: _Capture()
            try:
                admin_db = session.merge(admin)
                svc.issue_invoice(
                    session, invoice, reviewer=admin_db,
                    issued_url="https://invoice.example.com/INV_TEST.pdf",
                )
            finally:
                email_mod.get_email_backend = original

            self.assertEqual(len(captured), 1)
            msg = captured[0]
            self.assertEqual(msg.to, "buyer@example.com")
            self.assertIn(invoice.invoice_no, msg.subject)
            self.assertIn(invoice.invoice_no, msg.body_text)
            self.assertIn("¥39.00", msg.body_text)
            self.assertIn("https://invoice.example.com/INV_TEST.pdf", msg.body_text)
            self.assertIsNotNone(msg.body_html)
            self.assertIn("INV_TEST.pdf", msg.body_html)
        finally:
            session.close()


# ── factory: PAYMENT_ENABLED 开关 / 缺失字段保护 ───────────────────────────


class TestGatewayFactory(unittest.TestCase):
    def setUp(self) -> None:
        clear_gateway_overrides()
        self._saved = {
            k: os.environ.get(k) for k in [
                "PAYMENT_ENABLED",
                "WECHAT_PAY_APP_ID",
                "WECHAT_PAY_MCH_ID",
                "WECHAT_PAY_APIV3_KEY",
                "WECHAT_PAY_PLATFORM_CERT_PEM",
                "ALIPAY_APP_ID",
                "ALIPAY_PUBLIC_KEY_PEM",
            ]
        }
        for k in list(self._saved.keys()):
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        clear_gateway_overrides()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_disabled_returns_none(self):
        os.environ["PAYMENT_ENABLED"] = "false"
        self.assertIsNone(get_gateway("wechat"))
        self.assertIsNone(get_gateway("alipay"))

    def test_missing_keys_returns_none_even_when_enabled(self):
        os.environ["PAYMENT_ENABLED"] = "true"
        # 关键字段缺失
        self.assertIsNone(get_gateway("wechat"))
        self.assertIsNone(get_gateway("alipay"))

    def test_override_is_respected_regardless_of_env(self):
        os.environ["PAYMENT_ENABLED"] = "false"
        mock = _MockGateway("wechat")
        set_gateway_override("wechat", mock)
        self.assertIs(get_gateway("wechat"), mock)


# ── WechatGateway.place_order / refund ──────────────────────────────────────


class TestWechatGatewayPlaceOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.priv, cls.pub_pem = _gen_rsa_keypair()
        cls.apiv3_key = b"0123456789abcdef0123456789abcdef"
        cls.priv_pem = cls.priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        cls.gateway = WechatGateway(
            app_id="wxd678efh567hg6787",
            mch_id="1230000109",
            apiv3_key=cls.apiv3_key,
            platform_cert_pem=cls.pub_pem,
            cert_serial_no="SERIAL_001",
            merchant_private_key_pem=cls.priv_pem,
            notify_url="https://example.com/api/v1/billing/callbacks/wechat",
        )

    def test_place_order_success_returns_code_url(self):
        order = MagicMock()
        order.order_no = "DSA20260101AAAAAAAAAA"
        order.amount_cents = 3900
        order.plan_code = "pro"

        with unittest.mock.patch.object(
            self.gateway, "_http_post_v3",
            return_value={"code_url": "weixin://wxpay/bizpayurl?pr=12345"},
        ) as mock_post:
            result = self.gateway.place_order(order)

        self.assertEqual(result, "weixin://wxpay/bizpayurl?pr=12345")
        mock_post.assert_called_once()
        path, body = mock_post.call_args[0]
        self.assertEqual(path, "/v3/pay/transactions/native")
        self.assertEqual(body["out_trade_no"], "DSA20260101AAAAAAAAAA")
        self.assertEqual(body["amount"]["total"], 3900)
        self.assertEqual(body["mchid"], "1230000109")

    def test_place_order_missing_private_key_raises_gateway_error(self):
        from src.services.billing.gateways.base import GatewayError

        gw = WechatGateway(
            app_id="wx", mch_id="mch", apiv3_key=self.apiv3_key,
            platform_cert_pem=self.pub_pem, cert_serial_no="SER",
            notify_url="https://example.com/callbacks",
        )
        order = MagicMock()
        order.order_no = "DSA000"
        order.amount_cents = 3900
        with self.assertRaises(GatewayError):
            gw.place_order(order)

    def test_place_order_missing_notify_url_raises_gateway_error(self):
        from src.services.billing.gateways.base import GatewayError

        gw = WechatGateway(
            app_id="wx", mch_id="mch", apiv3_key=self.apiv3_key,
            platform_cert_pem=self.pub_pem, cert_serial_no="SER",
            merchant_private_key_pem=self.priv_pem,
        )
        order = MagicMock()
        order.order_no = "DSA001"
        order.amount_cents = 3900
        with self.assertRaises(GatewayError):
            gw.place_order(order)

    def test_place_order_api_error_propagates_as_gateway_error(self):
        from src.services.billing.gateways.base import GatewayError

        order = MagicMock()
        order.order_no = "DSA002"
        order.amount_cents = 3900
        with unittest.mock.patch.object(
            self.gateway, "_http_post_v3",
            side_effect=GatewayError("WeChat API HTTP 400: bad request"),
        ):
            with self.assertRaises(GatewayError):
                self.gateway.place_order(order)


class TestWechatGatewayRefund(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.priv, cls.pub_pem = _gen_rsa_keypair()
        cls.apiv3_key = b"0123456789abcdef0123456789abcdef"
        cls.priv_pem = cls.priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        cls.gateway = WechatGateway(
            app_id="wx", mch_id="mch", apiv3_key=cls.apiv3_key,
            platform_cert_pem=cls.pub_pem, cert_serial_no="SER001",
            merchant_private_key_pem=cls.priv_pem,
            notify_url="https://example.com/callbacks",
        )

    def test_refund_success_returns_refund_id(self):
        with unittest.mock.patch.object(
            self.gateway, "_http_post_v3",
            return_value={"refund_id": "WX_REFUND_REAL_001", "out_refund_no": "RF001"},
        ) as mock_post:
            result = self.gateway.refund(
                out_trade_no="DSA20260101AAAAAAAAAA",
                out_refund_no="RF001",
                amount_cents=3900,
                total_cents=3900,
                reason="测试退款",
            )
        self.assertEqual(result, "WX_REFUND_REAL_001")
        mock_post.assert_called_once()
        path, body = mock_post.call_args[0]
        self.assertEqual(path, "/v3/refund/domestic/refunds")
        self.assertEqual(body["out_trade_no"], "DSA20260101AAAAAAAAAA")
        self.assertEqual(body["amount"]["refund"], 3900)

    def test_refund_api_error_propagates(self):
        from src.services.billing.gateways.base import GatewayError

        with unittest.mock.patch.object(
            self.gateway, "_http_post_v3",
            side_effect=GatewayError("WeChat API HTTP 400"),
        ):
            with self.assertRaises(GatewayError):
                self.gateway.refund("DSA000", "RF000", 3900, 3900)


# ── AlipayGateway.place_order / refund ──────────────────────────────────────


class TestAlipayGatewayPlaceOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.priv, cls.pub_pem = _gen_rsa_keypair()
        cls.priv_pem = cls.priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        cls.gateway = AlipayGateway(
            app_id="2021000000000000",
            alipay_public_key_pem=cls.pub_pem,
            app_private_key_pem=cls.priv_pem,
            notify_url="https://example.com/api/v1/billing/callbacks/alipay",
        )

    def test_place_order_success_returns_qr_code(self):
        order = MagicMock()
        order.order_no = "DSA20260101BBBBBBBBBB"
        order.amount_cents = 3900
        order.plan_code = "pro"

        with unittest.mock.patch.object(
            self.gateway, "_call_openapi",
            return_value={"code": "10000", "qr_code": "https://qr.alipay.com/baxXXX"},
        ) as mock_call:
            result = self.gateway.place_order(order)

        self.assertEqual(result, "https://qr.alipay.com/baxXXX")
        mock_call.assert_called_once()
        method, biz = mock_call.call_args[0]
        self.assertEqual(method, "alipay.trade.precreate")
        self.assertEqual(biz["out_trade_no"], "DSA20260101BBBBBBBBBB")
        self.assertAlmostEqual(float(biz["total_amount"]), 39.00, places=2)

    def test_place_order_missing_private_key_raises_gateway_error(self):
        from src.services.billing.gateways.base import GatewayError

        gw = AlipayGateway(
            app_id="2021000000000000",
            alipay_public_key_pem=self.pub_pem,
        )
        order = MagicMock()
        order.order_no = "DSA000"
        order.amount_cents = 3900
        with self.assertRaises(GatewayError):
            gw.place_order(order)

    def test_place_order_api_error_propagates(self):
        from src.services.billing.gateways.base import GatewayError

        order = MagicMock()
        order.order_no = "DSA003"
        order.amount_cents = 3900
        with unittest.mock.patch.object(
            self.gateway, "_call_openapi",
            side_effect=GatewayError("Alipay alipay.trade.precreate 失败: code=40004"),
        ):
            with self.assertRaises(GatewayError):
                self.gateway.place_order(order)


class TestAlipayGatewayRefund(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.priv, cls.pub_pem = _gen_rsa_keypair()
        cls.priv_pem = cls.priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        cls.gateway = AlipayGateway(
            app_id="2021000000000000",
            alipay_public_key_pem=cls.pub_pem,
            app_private_key_pem=cls.priv_pem,
        )

    def test_refund_success_returns_trade_no(self):
        with unittest.mock.patch.object(
            self.gateway, "_call_openapi",
            return_value={"code": "10000", "trade_no": "ALIPAY_TXN_001"},
        ) as mock_call:
            result = self.gateway.refund(
                out_trade_no="DSA20260101BBBBBBBBBB",
                out_refund_no="RF20260101CCCCCCCCCC",
                amount_cents=3900,
                total_cents=3900,
                reason="七天无理由",
            )

        self.assertEqual(result, "ALIPAY_TXN_001")
        mock_call.assert_called_once()
        method, biz = mock_call.call_args[0]
        self.assertEqual(method, "alipay.trade.refund")
        self.assertEqual(biz["out_trade_no"], "DSA20260101BBBBBBBBBB")
        self.assertAlmostEqual(float(biz["refund_amount"]), 39.00, places=2)

    def test_refund_api_error_propagates(self):
        from src.services.billing.gateways.base import GatewayError

        with unittest.mock.patch.object(
            self.gateway, "_call_openapi",
            side_effect=GatewayError("Alipay alipay.trade.refund 失败: code=40004"),
        ):
            with self.assertRaises(GatewayError):
                self.gateway.refund("DSA000", "RF000", 3900, 3900)


# ─────────────────────────────────────────────────────────────────────────────
# fetch_settlements: WeChat tradebill + Alipay bill.downloadurl.query
# ─────────────────────────────────────────────────────────────────────────────


def _build_wechat_bill_csv(rows: list) -> bytes:
    """构造一份最小的微信账单 CSV（不压缩）。

    微信账单每字段前有 `` ` `` 前缀，最后一行以 ``总交易单数`` 开头。
    """
    header = ["`交易时间", "`商户订单号", "`微信支付订单号", "`交易状态", "`应结订单金额"]
    lines = [",".join(header)]
    for r in rows:
        line = ",".join(f"`{v}" for v in [
            r.get("时间", "2026-01-10 12:00:00"),
            r.get("out_trade_no", ""),
            r.get("txn_id", ""),
            r.get("状态", "SUCCESS"),
            r.get("金额", "39.00"),
        ])
        lines.append(line)
    lines.append("`总交易单数,`1,`总金额,`39.00")
    return "\n".join(lines).encode("utf-8")


def _build_alipay_bill_zip(rows: list) -> bytes:
    """构造一份最小的支付宝账单 ZIP（含一个明细 CSV，GBK 编码）。"""
    header = ["支付宝交易号", "商户订单号", "交易状态", "金额（元）", "付款时间"]
    csv_lines = [",".join(header)]
    for r in rows:
        csv_lines.append(",".join([
            r.get("trade_no", ""),
            r.get("out_trade_no", ""),
            r.get("状态", "交易成功"),
            r.get("金额", "39.00"),
            r.get("时间", "2026-01-10 12:00:00"),
        ]))
    csv_lines.append("------合计------")
    csv_content = "\n".join(csv_lines).encode("gbk")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("明细.csv", csv_content)
    return buf.getvalue()


class TestWechatGatewayFetchSettlements(unittest.TestCase):
    """WechatGateway.fetch_settlements: tradebill API + CSV 解析。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.priv, cls.pub_pem = _gen_rsa_keypair()
        cls.priv_pem = cls.priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        cls.gateway = WechatGateway(
            app_id="wxtest",
            mch_id="1234567890",
            apiv3_key="01234567890123456789012345678901",
            platform_cert_pem=cls.pub_pem,
            cert_serial_no="ABCDEF123456",
            merchant_private_key_pem=cls.priv_pem,
        )

    def test_parse_csv_returns_settlements(self):
        raw = _build_wechat_bill_csv([
            {"out_trade_no": "DSA001", "txn_id": "TXN001", "金额": "39.00"},
            {"out_trade_no": "DSA002", "txn_id": "TXN002", "金额": "9.90", "状态": "REFUND"},
        ])
        result = _parse_wechat_bill_csv(raw, "2026-01-10")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].out_trade_no, "DSA001")
        self.assertEqual(result[0].provider_trade_no, "TXN001")
        self.assertEqual(result[0].amount_cents, 3900)
        self.assertEqual(result[0].status, "paid")
        self.assertEqual(result[1].amount_cents, 990)
        self.assertEqual(result[1].status, "refund")

    def test_parse_csv_skips_summary_rows(self):
        raw = _build_wechat_bill_csv([{"out_trade_no": "DSA003", "txn_id": "TXN003"}])
        result = _parse_wechat_bill_csv(raw, "2026-01-10")
        for r in result:
            self.assertNotIn("总交易单数", r.out_trade_no)

    def test_parse_csv_skips_rows_without_order_no(self):
        raw = _build_wechat_bill_csv([{"out_trade_no": "", "txn_id": "TXN999"}])
        result = _parse_wechat_bill_csv(raw, "2026-01-10")
        self.assertEqual(len(result), 0)

    def test_parse_gzip_compressed(self):
        import gzip
        raw_csv = _build_wechat_bill_csv([{"out_trade_no": "DSA_GZ", "txn_id": "TXN_GZ", "金额": "1.00"}])
        compressed = gzip.compress(raw_csv)
        result = _parse_wechat_bill_csv(compressed, "2026-01-10")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].out_trade_no, "DSA_GZ")
        self.assertEqual(result[0].amount_cents, 100)

    def test_fetch_settlements_api_error_returns_empty(self):
        with unittest.mock.patch.object(
            self.gateway, "_http_get_v3",
            side_effect=Exception("网络错误"),
        ):
            result = self.gateway.fetch_settlements(__import__("datetime").date(2026, 1, 10))
        self.assertEqual(result, [])

    def test_fetch_settlements_no_download_url_returns_empty(self):
        with unittest.mock.patch.object(
            self.gateway, "_http_get_v3", return_value={"hash_type": "SHA1"},
        ):
            result = self.gateway.fetch_settlements(__import__("datetime").date(2026, 1, 10))
        self.assertEqual(result, [])

    def test_fetch_settlements_success(self):
        raw_csv = _build_wechat_bill_csv([
            {"out_trade_no": "DSA_OK", "txn_id": "TXN_OK", "金额": "39.00"},
        ])
        with unittest.mock.patch.object(
            self.gateway, "_http_get_v3",
            return_value={"download_url": "https://example.com/bill.csv.gz"},
        ), unittest.mock.patch.object(
            self.gateway, "_download_bill_file", return_value=raw_csv,
        ):
            result = self.gateway.fetch_settlements(__import__("datetime").date(2026, 1, 10))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].out_trade_no, "DSA_OK")
        self.assertEqual(result[0].amount_cents, 3900)


class TestAlipayGatewayFetchSettlements(unittest.TestCase):
    """AlipayGateway.fetch_settlements: bill.downloadurl.query + ZIP CSV 解析。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.priv, cls.pub_pem = _gen_rsa_keypair()
        cls.priv_pem = cls.priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        cls.gateway = AlipayGateway(
            app_id="2021000000000000",
            alipay_public_key_pem=cls.pub_pem,
            app_private_key_pem=cls.priv_pem,
        )

    def test_parse_zip_csv_returns_settlements(self):
        raw_zip = _build_alipay_bill_zip([
            {"trade_no": "ALIPAY001", "out_trade_no": "DSA001", "金额": "39.00"},
            {"trade_no": "ALIPAY002", "out_trade_no": "DSA002", "状态": "交易关闭", "金额": "0.00"},
        ])
        result = _parse_alipay_bill_csv(raw_zip, "2026-01-10")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].out_trade_no, "DSA001")
        self.assertEqual(result[0].provider_trade_no, "ALIPAY001")
        self.assertEqual(result[0].amount_cents, 3900)
        self.assertEqual(result[0].status, "paid")
        self.assertEqual(result[1].status, "closed")

    def test_parse_bad_zip_returns_empty(self):
        result = _parse_alipay_bill_csv(b"not a zip file", "2026-01-10")
        self.assertEqual(result, [])

    def test_parse_skips_summary_rows(self):
        raw_zip = _build_alipay_bill_zip([
            {"trade_no": "A1", "out_trade_no": "DSA_S", "金额": "10.00"},
        ])
        result = _parse_alipay_bill_csv(raw_zip, "2026-01-10")
        for r in result:
            self.assertNotIn("合计", r.out_trade_no)

    def test_parse_skips_rows_without_order_no(self):
        raw_zip = _build_alipay_bill_zip([{"trade_no": "A2", "out_trade_no": ""}])
        result = _parse_alipay_bill_csv(raw_zip, "2026-01-10")
        self.assertEqual(len(result), 0)

    def test_fetch_settlements_api_error_returns_empty(self):
        from src.services.billing.gateways.base import GatewayError
        with unittest.mock.patch.object(
            self.gateway, "_call_openapi",
            side_effect=GatewayError("API error"),
        ):
            result = self.gateway.fetch_settlements(__import__("datetime").date(2026, 1, 10))
        self.assertEqual(result, [])

    def test_fetch_settlements_no_url_returns_empty(self):
        with unittest.mock.patch.object(
            self.gateway, "_call_openapi", return_value={"code": "10000"},
        ):
            result = self.gateway.fetch_settlements(__import__("datetime").date(2026, 1, 10))
        self.assertEqual(result, [])

    def test_fetch_settlements_success(self):
        raw_zip = _build_alipay_bill_zip([
            {"trade_no": "ALIPAY_OK", "out_trade_no": "DSA_OK", "金额": "9.90"},
        ])
        with unittest.mock.patch.object(
            self.gateway, "_call_openapi",
            return_value={"code": "10000", "bill_download_url": "https://example.com/bill.zip"},
        ), unittest.mock.patch.object(
            self.gateway, "_download_bill_zip", return_value=raw_zip,
        ):
            result = self.gateway.fetch_settlements(__import__("datetime").date(2026, 1, 10))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].out_trade_no, "DSA_OK")
        self.assertEqual(result[0].amount_cents, 990)


# ─────────────────────────────────────────────────────────────────────────────
# security.py: IP 白名单 + 签名失败滑动窗口告警
# ─────────────────────────────────────────────────────────────────────────────


class _MockRequest:
    """最简 FastAPI Request 模拟, 仅用于 security 测试。"""

    def __init__(self, ip: str, forwarded_for: Optional[str] = None):
        self._headers = {}
        if forwarded_for:
            self._headers["x-forwarded-for"] = forwarded_for
        else:
            self._headers["x-real-ip"] = ip
        self.client = type("Client", (), {"host": ip})()

    @property
    def headers(self):
        return self._headers


class TestCheckCallbackIP(unittest.TestCase):
    """IP 白名单检查。"""

    def setUp(self):
        for key in (
            "PAYMENT_CALLBACK_ALLOWED_IPS",
            "PAYMENT_CALLBACK_ALLOWED_IPS_WECHAT",
            "PAYMENT_CALLBACK_ALLOWED_IPS_ALIPAY",
        ):
            os.environ.pop(key, None)

    def test_no_config_allows_all(self):
        from src.services.billing.security import check_callback_ip
        req = _MockRequest("1.2.3.4")
        self.assertTrue(check_callback_ip(req, "wechat"))

    def test_ip_in_range_allowed(self):
        from src.services.billing.security import check_callback_ip
        os.environ["PAYMENT_CALLBACK_ALLOWED_IPS"] = "101.226.103.0/24,140.207.0.0/16"
        req = _MockRequest("101.226.103.55")
        self.assertTrue(check_callback_ip(req, "wechat"))

    def test_ip_out_of_range_blocked(self):
        from src.services.billing.security import check_callback_ip
        os.environ["PAYMENT_CALLBACK_ALLOWED_IPS"] = "101.226.103.0/24"
        req = _MockRequest("8.8.8.8")
        self.assertFalse(check_callback_ip(req, "wechat"))

    def test_forwarded_for_takes_priority(self):
        from src.services.billing.security import check_callback_ip
        os.environ["PAYMENT_CALLBACK_ALLOWED_IPS"] = "10.0.0.0/8"
        req = _MockRequest("1.2.3.4", forwarded_for="10.0.1.5, 192.168.1.1")
        self.assertTrue(check_callback_ip(req, "alipay"))

    def test_per_provider_key_takes_priority(self):
        from src.services.billing.security import check_callback_ip
        os.environ["PAYMENT_CALLBACK_ALLOWED_IPS"] = "10.0.0.0/8"
        os.environ["PAYMENT_CALLBACK_ALLOWED_IPS_WECHAT"] = "203.0.113.0/24"
        req_allowed = _MockRequest("203.0.113.10")
        req_blocked = _MockRequest("10.0.0.1")
        self.assertTrue(check_callback_ip(req_allowed, "wechat"))
        self.assertFalse(check_callback_ip(req_blocked, "wechat"))

    def test_single_ip_exact_match(self):
        from src.services.billing.security import check_callback_ip
        os.environ["PAYMENT_CALLBACK_ALLOWED_IPS"] = "192.0.2.1"
        self.assertTrue(check_callback_ip(_MockRequest("192.0.2.1"), "alipay"))
        self.assertFalse(check_callback_ip(_MockRequest("192.0.2.2"), "alipay"))


class TestSigFailureAlert(unittest.TestCase):
    """签名失败滑动窗口告警。"""

    def setUp(self):
        import src.services.billing.security as sec
        sec._sig_fail_windows.clear()
        sec._last_alert_times.clear()
        for key in (
            "PAYMENT_CALLBACK_SIG_FAIL_THRESHOLD",
            "PAYMENT_CALLBACK_SIG_FAIL_WINDOW_SECONDS",
        ):
            os.environ.pop(key, None)

    def test_no_alert_below_threshold(self):
        from src.services.billing.security import record_sig_failure
        os.environ["PAYMENT_CALLBACK_SIG_FAIL_THRESHOLD"] = "3"
        os.environ["PAYMENT_CALLBACK_SIG_FAIL_WINDOW_SECONDS"] = "60"
        with unittest.mock.patch(
            "src.services.billing.security._notify_admin"
        ) as mock_notify:
            record_sig_failure("wechat")
            record_sig_failure("wechat")
            mock_notify.assert_not_called()

    def test_alert_on_threshold_reached(self):
        from src.services.billing.security import record_sig_failure
        os.environ["PAYMENT_CALLBACK_SIG_FAIL_THRESHOLD"] = "3"
        os.environ["PAYMENT_CALLBACK_SIG_FAIL_WINDOW_SECONDS"] = "60"
        with unittest.mock.patch(
            "src.services.billing.security._notify_admin"
        ) as mock_notify:
            for _ in range(3):
                record_sig_failure("wechat")
            mock_notify.assert_called_once()
            subject, body = mock_notify.call_args[0]
            self.assertIn("wechat", subject)
            self.assertIn("3 次", body)

    def test_cooldown_prevents_duplicate_alert(self):
        from src.services.billing.security import record_sig_failure
        import src.services.billing.security as sec
        os.environ["PAYMENT_CALLBACK_SIG_FAIL_THRESHOLD"] = "2"
        os.environ["PAYMENT_CALLBACK_SIG_FAIL_WINDOW_SECONDS"] = "60"
        with unittest.mock.patch(
            "src.services.billing.security._notify_admin"
        ) as mock_notify:
            for _ in range(4):
                record_sig_failure("alipay")
            self.assertEqual(mock_notify.call_count, 1)

    def test_different_providers_independent(self):
        from src.services.billing.security import record_sig_failure
        os.environ["PAYMENT_CALLBACK_SIG_FAIL_THRESHOLD"] = "2"
        os.environ["PAYMENT_CALLBACK_SIG_FAIL_WINDOW_SECONDS"] = "60"
        with unittest.mock.patch(
            "src.services.billing.security._notify_admin"
        ) as mock_notify:
            for _ in range(2):
                record_sig_failure("wechat")
            for _ in range(2):
                record_sig_failure("alipay")
            self.assertEqual(mock_notify.call_count, 2)


if __name__ == "__main__":
    unittest.main()
