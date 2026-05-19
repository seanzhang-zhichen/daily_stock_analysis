# -*- coding: utf-8 -*-
"""微信支付 Native (V3) gateway。

本文件**只用 ``cryptography`` 标准能力**完成 V3 回调签名校验与回调密文解密,
不依赖第三方微信 SDK。下单 / 退款 / 拉账单需要的 OAuth 签名计算同样可用纯
``cryptography`` 实现, 但本切片只完成回调验签 (业务最关键的安全闭环), 真实
``place_order`` / ``refund`` / ``fetch_settlements`` 留待 SDK 切片接入。

签名校验流程参考微信支付 V3 官方文档:

1. 取 headers ``Wechatpay-Timestamp`` / ``Wechatpay-Nonce`` /
   ``Wechatpay-Signature`` / ``Wechatpay-Serial``。
2. 构造签名串: ``f"{timestamp}\\n{nonce}\\n{body}\\n"``。
3. 用平台证书公钥验签 (RSA-SHA256, PKCS#1 v1.5)。
4. 用 APIv3 密钥对 ``resource.ciphertext`` 做 AES-256-GCM 解密。
5. 解密后 JSON 即业务字段。

平台证书可由 ``WECHAT_PAY_PLATFORM_CERT_PATH`` 环境变量指向, 也可在构造
时传入 PEM 字符串 (便于单元测试)。
"""

from __future__ import annotations

import base64
import csv
import gzip
import io
import json
import logging
import os
import random
import string
import time
from datetime import date, datetime
from typing import List, Optional

from src.services.billing.gateways.base import (
    CallbackResult,
    ChannelSettlement,
    GatewayError,
    PaymentGateway,
)

logger = logging.getLogger(__name__)


# 回调时间戳偏差容忍 (秒) — 微信官方建议 5 分钟。
_TIMESTAMP_TOLERANCE_SECONDS = 5 * 60


class WechatGateway(PaymentGateway):
    """微信支付 Native gateway (V3)。"""

    provider = "wechat"

    def __init__(
        self,
        app_id: str,
        mch_id: str,
        apiv3_key: str,
        platform_cert_pem: str,
        cert_serial_no: str = "",
        merchant_private_key_pem: str = "",
        notify_url: Optional[str] = None,
    ) -> None:
        self.app_id = app_id
        self.mch_id = mch_id
        self.apiv3_key = apiv3_key.encode("utf-8") if isinstance(apiv3_key, str) else apiv3_key
        self.platform_cert_pem = platform_cert_pem
        self.cert_serial_no = cert_serial_no
        self.merchant_private_key_pem = merchant_private_key_pem
        self.notify_url = notify_url

    # ── 内部: 平台证书加载 ─────────────────────────────────────────────────

    def _load_platform_public_key(self):  # type: ignore[no-untyped-def]
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import (
            load_pem_public_key,
        )

        pem = self.platform_cert_pem.strip()
        if not pem:
            raise RuntimeError("WechatGateway: platform cert PEM is empty")
        # 同时兼容证书 PEM 与 公钥 PEM
        if "BEGIN CERTIFICATE" in pem:
            cert = x509.load_pem_x509_certificate(pem.encode("utf-8"))
            return cert.public_key()
        return load_pem_public_key(pem.encode("utf-8"))

    # ── 验签 ───────────────────────────────────────────────────────────────

    def verify_callback(self, headers: dict, body: bytes) -> CallbackResult:
        ts = (headers.get("Wechatpay-Timestamp") or headers.get("wechatpay-timestamp") or "").strip()
        nonce = (headers.get("Wechatpay-Nonce") or headers.get("wechatpay-nonce") or "").strip()
        sig_b64 = (headers.get("Wechatpay-Signature") or headers.get("wechatpay-signature") or "").strip()
        serial = (headers.get("Wechatpay-Serial") or headers.get("wechatpay-serial") or "").strip()

        body_text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
        event_id_fallback = f"wechat-{serial}-{ts}-{nonce}" if (serial or ts or nonce) else f"wechat-{int(time.time() * 1000)}"

        result = CallbackResult(
            provider=self.provider,
            signature_valid=False,
            event_id=event_id_fallback,
            event_type="callback.received",
            raw_payload=body_text[:4096],
        )

        # 1) 时间戳校验
        try:
            ts_int = int(ts)
            if abs(time.time() - ts_int) > _TIMESTAMP_TOLERANCE_SECONDS:
                logger.warning("wechat callback timestamp skew too large: %s", ts)
                return result
        except (TypeError, ValueError):
            logger.warning("wechat callback missing/invalid timestamp header")
            return result

        if not (nonce and sig_b64 and body_text):
            logger.warning("wechat callback missing nonce/signature/body")
            return result

        # 2) 签名校验
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding

            pubkey = self._load_platform_public_key()
            signed_str = f"{ts}\n{nonce}\n{body_text}\n".encode("utf-8")
            signature = base64.b64decode(sig_b64)
            pubkey.verify(
                signature,
                signed_str,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            result.signature_valid = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("wechat callback signature invalid: %s", exc)
            return result

        # 3) 解密 resource.ciphertext (AES-256-GCM, key = APIv3 密钥)
        try:
            payload = json.loads(body_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wechat callback body not json: %s", exc)
            result.parse_error = True
            return result

        result.event_id = str(payload.get("id") or event_id_fallback)[:128]
        event_type_raw = str(payload.get("event_type") or "").upper()
        resource = payload.get("resource") or {}
        ciphertext_b64 = resource.get("ciphertext") or ""
        nonce_r = resource.get("nonce") or ""
        associated_data = resource.get("associated_data") or ""

        if not ciphertext_b64:
            # 不含 resource 的事件 (如 test) — 标记 parse_error 但保留 signature_valid
            result.parse_error = True
            return result

        try:
            plaintext = _aes_gcm_decrypt(
                key=self.apiv3_key,
                nonce=nonce_r.encode("utf-8"),
                associated_data=associated_data.encode("utf-8"),
                ciphertext_b64=ciphertext_b64,
            )
            data = json.loads(plaintext)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wechat resource decrypt failed: %s", exc)
            result.parse_error = True
            return result

        result.raw_event = data
        result.out_trade_no = (data.get("out_trade_no") or "")[:32] or None
        result.provider_trade_no = (data.get("transaction_id") or "")[:64] or None
        amount = data.get("amount") or {}
        try:
            result.amount_cents = int(amount.get("total") or 0)
        except (TypeError, ValueError):
            result.amount_cents = 0

        trade_state = str(data.get("trade_state") or "").upper()
        if trade_state == "SUCCESS":
            result.status = "paid"
            result.event_type = "pay.success"
        elif trade_state in ("CLOSED", "REVOKED", "PAYERROR"):
            result.status = "failed" if trade_state == "PAYERROR" else "closed"
            result.event_type = "pay.fail"
        elif trade_state == "REFUND":
            result.status = "refunded"
            result.event_type = "refund.success"
        else:
            result.status = trade_state.lower() or "unknown"
            result.event_type = (event_type_raw.lower() or "callback.received")
        return result


    # ── 内部: 商户私钥 + 请求签名 ─────────────────────────────────────────

    def _load_merchant_private_key(self):  # type: ignore[no-untyped-def]
        """加载商户私钥 (wechat_apiclient_key.pem)，用于签名下单 / 退款请求。"""
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        pem = self.merchant_private_key_pem.strip()
        if not pem:
            raise GatewayError(
                "WechatGateway: merchant_private_key_pem 未配置 — 无法签名请求。"
                "请设置 WECHAT_PAY_PRIVATE_KEY_PEM 或 WECHAT_PAY_PRIVATE_KEY_PATH。"
            )
        return load_pem_private_key(pem.encode("utf-8"), password=None)

    def _build_auth_header(self, method: str, url_path: str, body: str) -> str:
        """构建微信支付 V3 Authorization 请求头。

        签名串格式::

            {HTTP_METHOD}\\n{URL_PATH}\\n{TIMESTAMP}\\n{NONCE_STR}\\n{BODY}\\n
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        if not self.cert_serial_no:
            raise GatewayError(
                "WechatGateway: cert_serial_no 未配置 — 无法构建 Authorization header。"
                "请设置 WECHAT_PAY_CERT_SERIAL_NO。"
            )

        nonce = "".join(random.choices(string.ascii_uppercase + string.digits, k=32))
        ts = str(int(time.time()))

        signed_str = f"{method}\n{url_path}\n{ts}\n{nonce}\n{body}\n".encode("utf-8")
        priv = self._load_merchant_private_key()
        sig = priv.sign(signed_str, padding.PKCS1v15(), hashes.SHA256())
        sig_b64 = base64.b64encode(sig).decode("ascii")

        return (
            f'WECHATPAY2-SHA256-RSA2048 mchid="{self.mch_id}",'
            f'nonce_str="{nonce}",timestamp="{ts}",'
            f'serial_no="{self.cert_serial_no}",signature="{sig_b64}"'
        )

    def _http_post_v3(self, url_path: str, body_dict: dict) -> dict:
        """向微信 V3 API 发 POST 请求，返回响应 JSON。"""
        import urllib.error
        import urllib.request

        body = json.dumps(body_dict, ensure_ascii=False)
        auth = self._build_auth_header("POST", url_path, body)
        req = urllib.request.Request(
            f"https://api.mch.weixin.qq.com{url_path}",
            data=body.encode("utf-8"),
            headers={
                "Authorization": auth,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "DSA-Payment/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_err = exc.read().decode("utf-8", errors="replace")
            raise GatewayError(f"WeChat API HTTP {exc.code}: {body_err}") from exc

    def _http_get_v3(self, url_path: str) -> dict:
        """向微信 V3 API 发 GET 请求，返回响应 JSON。"""
        import urllib.error
        import urllib.request

        auth = self._build_auth_header("GET", url_path, "")
        req = urllib.request.Request(
            f"https://api.mch.weixin.qq.com{url_path}",
            headers={
                "Authorization": auth,
                "Accept": "application/json",
                "User-Agent": "DSA-Payment/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_err = exc.read().decode("utf-8", errors="replace")
            raise GatewayError(f"WeChat API HTTP {exc.code}: {body_err}") from exc

    # ── 下单 / 退款 ────────────────────────────────────────────────────────

    def place_order(self, order, notify_url: Optional[str] = None) -> str:
        """微信 Native 下单，返回 ``code_url`` 供前端渲染二维码。

        Args:
            order: :class:`AppOrder` (鸭子类型，依赖 order_no / amount_cents / plan_code)。
            notify_url: 异步回调地址；留空时使用初始化时的 ``notify_url``。

        Returns:
            ``code_url`` 字符串，例如 ``weixin://wxpay/bizpayurl?pr=...``。

        Raises:
            :class:`GatewayError`: 私钥未配置 / API 返回错误 / 缺少 code_url。
        """
        notify = notify_url or self.notify_url or ""
        if not notify:
            raise GatewayError("WECHAT_PAY_NOTIFY_URL 未配置，无法发起下单")

        data = self._http_post_v3(
            "/v3/pay/transactions/native",
            {
                "appid": self.app_id,
                "mchid": self.mch_id,
                "description": f"DSA Pro 订阅 - {getattr(order, 'plan_code', '')}",
                "out_trade_no": order.order_no,
                "notify_url": notify,
                "amount": {"total": order.amount_cents, "currency": "CNY"},
            },
        )
        code_url = data.get("code_url") or ""
        if not code_url:
            raise GatewayError(f"WeChat place_order: 缺少 code_url，响应: {data}")
        logger.info("wechat place_order ok: order=%s", order.order_no)
        return code_url

    def refund(
        self,
        out_trade_no: str,
        out_refund_no: str,
        amount_cents: int,
        total_cents: int,
        reason: Optional[str] = None,
    ) -> str:
        """向微信发起退款，返回通道退款单号 (refund_id)。

        Raises:
            :class:`GatewayError`: 签名失败 / API 返回错误 / 缺少 refund_id。
        """
        body: dict = {
            "out_trade_no": out_trade_no,
            "out_refund_no": out_refund_no,
            "reason": reason or "用户申请退款",
            "amount": {
                "refund": amount_cents,
                "total": total_cents,
                "currency": "CNY",
            },
        }
        if self.notify_url:
            body["notify_url"] = self.notify_url

        data = self._http_post_v3("/v3/refund/domestic/refunds", body)
        refund_id = data.get("refund_id") or data.get("out_refund_no") or ""
        if not refund_id:
            raise GatewayError(f"WeChat refund: 缺少 refund_id，响应: {data}")
        logger.info("wechat refund ok: out_trade_no=%s refund_id=%s", out_trade_no, refund_id)
        return refund_id


    def _download_bill_file(self, download_url: str) -> bytes:
        """下载微信账单文件（gzip 压缩 CSV），返回原始字节。

        微信 V3 账单下载 URL 不再需要额外签名，直接 GET 即可。
        """
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "DSA-Payment/1.0", "Accept": "*/*"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            body_err = exc.read().decode("utf-8", errors="replace")
            raise GatewayError(f"WeChat bill download HTTP {exc.code}: {body_err}") from exc

    def fetch_settlements(self, target_date: date) -> List[ChannelSettlement]:
        """拉取目标日微信账单，返回规范化 :class:`ChannelSettlement` 列表。

        流程:

        1. ``GET /v3/bill/tradebill?bill_date={YYYY-MM-DD}&bill_type=ALL``
           取 ``download_url``。
        2. 下载 gzip 压缩 CSV（无需再次签名）。
        3. 解压后按行解析, 提取商户订单号 / 微信订单号 / 应结金额 / 状态 / 交易时间。

        WeChat V3 账单 CSV 结构（每行首字符为 `` ` ``，需去除）:
        ``交易时间, 公众账号ID, 商户号, ..., 商户订单号, 微信支付订单号, ..., 交易状态, ..., 应结订单金额, ...``

        失败时返回空列表 + 日志（不抛异常，对账脚本可降级为仅 local_only 差异）。
        """
        date_str = target_date.strftime("%Y-%m-%d")
        url_path = f"/v3/bill/tradebill?bill_date={date_str}&bill_type=ALL"

        try:
            resp = self._http_get_v3(url_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wechat fetch_settlements: tradebill API error date=%s: %s", date_str, exc)
            return []

        download_url = resp.get("download_url") or ""
        if not download_url:
            logger.warning(
                "wechat fetch_settlements: no download_url in response date=%s resp=%s",
                date_str, resp,
            )
            return []

        try:
            raw_bytes = self._download_bill_file(download_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wechat fetch_settlements: download failed date=%s: %s", date_str, exc)
            return []

        return _parse_wechat_bill_csv(raw_bytes, date_str)


def _parse_wechat_bill_csv(raw_bytes: bytes, date_str: str) -> List[ChannelSettlement]:
    """解析微信账单 CSV（gzip 压缩），返回 :class:`ChannelSettlement` 列表。

    微信账单格式要点:
    - 整个文件 gzip 压缩。
    - CSV 每个字段前有 `` ` `` 前缀，需剥离。
    - 第一行为列标题，最后两行为汇总行（以 ``总交易单数`` 开头），跳过。
    - 关键列名（中文）：``交易时间`` / ``商户订单号`` / ``微信支付订单号`` /
      ``交易状态`` / ``应结订单金额``。
    """
    try:
        content = gzip.decompress(raw_bytes).decode("utf-8", errors="replace")
    except (gzip.BadGzipFile, OSError, Exception):
        content = raw_bytes.decode("utf-8", errors="replace")

    results: List[ChannelSettlement] = []

    def _strip(s: str) -> str:
        return s.lstrip("`").strip()

    reader = csv.reader(io.StringIO(content))
    header: List[str] = []
    for row in reader:
        if not row:
            continue
        stripped = [_strip(c) for c in row]
        if not header:
            header = stripped
            continue
        if not stripped[0] or stripped[0].startswith("总交易单数") or stripped[0].startswith("合计"):
            continue
        record = dict(zip(header, stripped))
        out_trade_no = record.get("商户订单号", "").strip()
        txn_id = record.get("微信支付订单号", "").strip()
        trade_state = record.get("交易状态", "").strip()
        amount_raw = record.get("应结订单金额", "0").strip()
        time_raw = record.get("交易时间", "").strip()

        if not out_trade_no:
            continue

        try:
            amount_cents = int(round(float(amount_raw) * 100))
        except (TypeError, ValueError):
            amount_cents = 0

        try:
            settled_at = datetime.strptime(time_raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            settled_at = datetime.strptime(date_str, "%Y-%m-%d")

        status = "paid" if trade_state in ("SUCCESS", "TRADE_SUCCESS") else trade_state.lower() or "unknown"

        results.append(ChannelSettlement(
            provider="wechat",
            provider_trade_no=txn_id or out_trade_no,
            out_trade_no=out_trade_no,
            amount_cents=amount_cents,
            status=status,
            settled_at=settled_at,
            raw=record,
        ))

    logger.info("wechat fetch_settlements parsed %d rows for %s", len(results), date_str)
    return results


def _aes_gcm_decrypt(
    key: bytes,
    nonce: bytes,
    associated_data: bytes,
    ciphertext_b64: str,
) -> bytes:
    """微信 V3 ``resource.ciphertext`` 解密。

    ciphertext = base64( aes_256_gcm_ciphertext + 16-byte_tag )
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.b64decode(ciphertext_b64)
    aes = AESGCM(key)
    return aes.decrypt(nonce, raw, associated_data)


__all__ = ["WechatGateway"]
