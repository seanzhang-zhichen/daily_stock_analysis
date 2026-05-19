# -*- coding: utf-8 -*-
"""支付宝 PC 网站支付 gateway。

只使用 ``cryptography`` 实现 RSA2 (SHA256withRSA) 异步通知验签, 不依赖第三方
``python-alipay-sdk`` 等库。下单 / 退款 / 拉账单的 OpenAPI 请求签名结构与
验签一致, 同样可用 ``cryptography`` 实现, 但本切片只完成回调验签 (业务最关
键的安全闭环), 真实 ``place_order`` / ``refund`` / ``fetch_settlements``
留待后续 SDK 切片接入。

异步通知验签流程参考支付宝官方文档:

1. 取出 form-urlencoded 全部参数, 移除 ``sign`` 与 ``sign_type``。
2. 按 ASCII 升序排序剩余 key, 用 ``&`` 拼接 ``k=v`` (空 value 也参与拼接,
   按 alipay 文档实际默认会跳过空值; 这里同步 alipay-sdk-python 行为只拼接
   非空 value)。
3. 用支付宝平台公钥验签 (RSA-SHA256, PKCS#1 v1.5)。
4. 校验 ``app_id`` 与本应用一致 (防止张冠李戴攻击)。
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import time
import urllib.parse
import zipfile
from datetime import date, datetime as _dt
from typing import List, Optional

from src.services.billing.gateways.base import CallbackResult, ChannelSettlement, GatewayError, PaymentGateway

logger = logging.getLogger(__name__)


_TIMESTAMP_TOLERANCE_SECONDS = 10 * 60  # 支付宝异步通知一般在 10min 内


class AlipayGateway(PaymentGateway):
    """支付宝 PC 网站支付 gateway。"""

    provider = "alipay"

    def __init__(
        self,
        app_id: str,
        alipay_public_key_pem: str,
        app_private_key_pem: str = "",
        notify_url: Optional[str] = None,
        return_url: Optional[str] = None,
    ) -> None:
        self.app_id = app_id
        self.alipay_public_key_pem = alipay_public_key_pem
        self.app_private_key_pem = app_private_key_pem
        self.notify_url = notify_url
        self.return_url = return_url

    # ── 内部: 平台公钥加载 ─────────────────────────────────────────────────

    def _load_alipay_public_key(self):  # type: ignore[no-untyped-def]
        from cryptography.hazmat.primitives.serialization import (
            load_pem_public_key,
        )
        from cryptography import x509

        pem = self.alipay_public_key_pem.strip()
        if not pem:
            raise RuntimeError("AlipayGateway: alipay public key is empty")
        if "BEGIN CERTIFICATE" in pem:
            cert = x509.load_pem_x509_certificate(pem.encode("utf-8"))
            return cert.public_key()
        return load_pem_public_key(pem.encode("utf-8"))

    # ── 验签 ───────────────────────────────────────────────────────────────

    def verify_callback(self, headers: dict, body: bytes) -> CallbackResult:
        body_text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)

        # urllib.parse.parse_qsl 已经做了 URL decode
        try:
            pairs = urllib.parse.parse_qsl(body_text, keep_blank_values=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("alipay callback body not form: %s", exc)
            return CallbackResult(
                provider=self.provider,
                signature_valid=False,
                event_id=f"alipay-{int(time.time()*1000)}",
                raw_payload=body_text[:4096],
                parse_error=True,
            )

        params = {k: v for k, v in pairs}
        sign_b64 = params.pop("sign", "")
        sign_type = params.pop("sign_type", "RSA2") or "RSA2"

        event_id = (
            params.get("notify_id")
            or f"alipay-{params.get('out_trade_no', '')}-{params.get('gmt_payment', '')}"
            or f"alipay-{int(time.time()*1000)}"
        )

        result = CallbackResult(
            provider=self.provider,
            signature_valid=False,
            event_id=event_id[:128],
            event_type="callback.received",
            raw_payload=body_text[:4096],
        )

        # 1) 时间戳偏差 (notify_time, 格式 YYYY-MM-DD HH:MM:SS)
        notify_time_raw = params.get("notify_time") or ""
        if notify_time_raw:
            try:
                from datetime import datetime as _dt
                ts = _dt.strptime(notify_time_raw, "%Y-%m-%d %H:%M:%S").timestamp()
                if abs(time.time() - ts) > _TIMESTAMP_TOLERANCE_SECONDS:
                    logger.warning("alipay notify_time skew too large: %s", notify_time_raw)
                    # 仅记录, 不直接判失败 — 部分通知会有延迟; 真实校验由签名 + app_id 兜底
            except ValueError:
                logger.debug("alipay notify_time parse failed: %s", notify_time_raw)

        # 2) app_id 校验
        if str(params.get("app_id") or "") != str(self.app_id or ""):
            logger.warning(
                "alipay callback app_id mismatch: got=%s expected=%s",
                params.get("app_id"),
                self.app_id,
            )
            return result

        if not sign_b64:
            logger.warning("alipay callback missing sign")
            return result

        # 3) 构建签名串 — 按 ASCII 升序, 跳过空 value
        try:
            sorted_items = sorted(
                ((k, v) for k, v in params.items() if v not in (None, "")),
                key=lambda kv: kv[0],
            )
            signed_str = "&".join(f"{k}={v}" for k, v in sorted_items).encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("alipay build sign string failed: %s", exc)
            return result

        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding

            pubkey = self._load_alipay_public_key()
            algo = hashes.SHA256() if sign_type.upper() == "RSA2" else hashes.SHA1()
            signature = base64.b64decode(sign_b64)
            pubkey.verify(
                signature,
                signed_str,
                padding.PKCS1v15(),
                algo,
            )
            result.signature_valid = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("alipay callback signature invalid: %s", exc)
            return result

        # 4) 规范化业务字段
        result.raw_event = params
        result.out_trade_no = (params.get("out_trade_no") or "")[:32] or None
        result.provider_trade_no = (params.get("trade_no") or "")[:64] or None

        # alipay total_amount 单位是 元 (带 2 位小数), 转换为分
        total_amount_raw = params.get("total_amount") or "0"
        try:
            result.amount_cents = int(round(float(total_amount_raw) * 100))
        except (TypeError, ValueError):
            result.amount_cents = 0

        trade_status = (params.get("trade_status") or "").upper()
        if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            result.status = "paid"
            result.event_type = "pay.success"
        elif trade_status == "TRADE_CLOSED":
            result.status = "closed"
            result.event_type = "pay.fail"
        elif trade_status == "WAIT_BUYER_PAY":
            result.status = "pending"
            result.event_type = "callback.received"
        else:
            result.status = trade_status.lower() or "unknown"
            result.event_type = "callback.received"

        return result

    # ── 内部: App 私钥加载 + 请求签名 ─────────────────────────────────────

    def _load_app_private_key(self):  # type: ignore[no-untyped-def]
        """加载 App 私钥，用于签名下单 / 退款等请求。"""
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        pem = self.app_private_key_pem.strip()
        if not pem:
            raise GatewayError(
                "AlipayGateway: app_private_key_pem 未配置 — 无法签名请求。"
                "请设置 ALIPAY_APP_PRIVATE_KEY_PEM 或 ALIPAY_APP_PRIVATE_KEY_PATH。"
            )
        return load_pem_private_key(pem.encode("utf-8"), password=None)

    def _build_sign_params(self, method: str, biz_content: dict) -> dict:
        """构建支付宝 OpenAPI 请求参数（不含 sign 字段）。"""
        params: dict = {
            "app_id": self.app_id,
            "method": method,
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content, ensure_ascii=False),
        }
        if self.notify_url:
            params["notify_url"] = self.notify_url
        return params

    def _sign_params(self, params: dict) -> str:
        """对参数字典构建签名串并用 RSA2 签名，返回 Base64 字符串。"""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        sorted_items = sorted(
            ((k, v) for k, v in params.items() if v not in (None, "")),
            key=lambda kv: kv[0],
        )
        signed_str = "&".join(f"{k}={v}" for k, v in sorted_items).encode("utf-8")
        priv = self._load_app_private_key()
        sig = priv.sign(signed_str, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode("ascii")

    def _call_openapi(self, method: str, biz_content: dict) -> dict:
        """调用支付宝 OpenAPI，返回响应内层字典 (已检查 code==10000)。

        Raises:
            :class:`GatewayError`: 签名失败 / HTTP 错误 / API 返回 code != 10000。
        """
        import urllib.error
        import urllib.request

        params = self._build_sign_params(method, biz_content)
        params["sign"] = self._sign_params(params)

        body = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(
            "https://openapi.alipay.com/gateway.do",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "User-Agent": "DSA-Payment/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body_err = exc.read().decode("utf-8", errors="replace")
            raise GatewayError(f"Alipay API HTTP {exc.code}: {body_err}") from exc

        resp_json = json.loads(raw)
        resp_key = method.replace(".", "_") + "_response"
        inner = resp_json.get(resp_key) or {}

        code = str(inner.get("code") or "")
        if code != "10000":
            raise GatewayError(
                f"Alipay {method} 失败: code={code} "
                f"sub_code={inner.get('sub_code')} "
                f"msg={inner.get('msg')} sub_msg={inner.get('sub_msg')}"
            )
        return inner

    # ── 下单 / 退款 ────────────────────────────────────────────────────────

    def place_order(self, order, notify_url: Optional[str] = None) -> str:
        """支付宝扫码支付下单（alipay.trade.precreate），返回 ``qr_code`` URL。

        Args:
            order: :class:`AppOrder` (鸭子类型，依赖 order_no / amount_cents / plan_code)。
            notify_url: 异步回调地址；留空时使用初始化时的 ``notify_url``。

        Returns:
            ``qr_code`` 字符串，例如 ``https://qr.alipay.com/bax...``。

        Raises:
            :class:`GatewayError`: 私钥未配置 / API 返回错误 / 缺少 qr_code。
        """
        if notify_url:
            self.notify_url = notify_url

        biz: dict = {
            "out_trade_no": order.order_no,
            "total_amount": f"{(order.amount_cents or 0) / 100:.2f}",
            "subject": f"DSA Pro 订阅 - {getattr(order, 'plan_code', '')}",
        }
        data = self._call_openapi("alipay.trade.precreate", biz)
        qr_code = data.get("qr_code") or ""
        if not qr_code:
            raise GatewayError(f"Alipay place_order: 缺少 qr_code，响应: {data}")
        logger.info("alipay place_order ok: order=%s", order.order_no)
        return qr_code

    def refund(
        self,
        out_trade_no: str,
        out_refund_no: str,
        amount_cents: int,
        total_cents: int,
        reason: Optional[str] = None,
    ) -> str:
        """向支付宝发起退款，成功后返回 ``trade_no`` 作为凭证。

        Raises:
            :class:`GatewayError`: 签名失败 / API 返回错误。
        """
        biz: dict = {
            "out_trade_no": out_trade_no,
            "refund_amount": f"{amount_cents / 100:.2f}",
            "out_request_no": out_refund_no,
            "refund_reason": reason or "用户申请退款",
        }
        data = self._call_openapi("alipay.trade.refund", biz)
        refund_id = data.get("trade_no") or out_refund_no
        logger.info(
            "alipay refund ok: out_trade_no=%s out_refund_no=%s trade_no=%s",
            out_trade_no, out_refund_no, refund_id,
        )
        return refund_id

    def fetch_settlements(self, target_date: date) -> List[ChannelSettlement]:
        """拉取目标日支付宝账单，返回规范化 :class:`ChannelSettlement` 列表。

        流程:

        1. 调用 ``alipay.bill.downloadurl.query`` 获取账单下载地址。
        2. 下载 ZIP 文件，解压内含 CSV（账单明细文件）。
        3. 解析 CSV，提取商户订单号 / 支付宝交易号 / 金额 / 状态 / 时间。

        支付宝账单 CSV 关键列（含在 ZIP 内的明细文件）:
        ``支付宝交易号``, ``商户订单号``, ``交易状态``, ``金额（元）``, ``付款时间``

        失败时返回空列表 + 日志（不抛异常）。
        """
        date_str = target_date.strftime("%Y-%m-%d")

        try:
            data = self._call_openapi(
                "alipay.bill.downloadurl.query",
                {"bill_type": "trade", "bill_date": date_str},
            )
        except GatewayError as exc:
            logger.warning("alipay fetch_settlements: API error date=%s: %s", date_str, exc)
            return []

        download_url = data.get("bill_download_url") or ""
        if not download_url:
            logger.warning(
                "alipay fetch_settlements: no bill_download_url date=%s resp=%s",
                date_str, data,
            )
            return []

        try:
            raw_bytes = self._download_bill_zip(download_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("alipay fetch_settlements: download failed date=%s: %s", date_str, exc)
            return []

        return _parse_alipay_bill_csv(raw_bytes, date_str)

    def _download_bill_zip(self, download_url: str) -> bytes:
        """下载支付宝账单 ZIP 文件，返回原始字节。"""
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
            raise GatewayError(f"Alipay bill download HTTP {exc.code}: {body_err}") from exc


def _parse_alipay_bill_csv(raw_bytes: bytes, date_str: str) -> List[ChannelSettlement]:
    """解析支付宝账单 ZIP 内的明细 CSV，返回 :class:`ChannelSettlement` 列表。

    支付宝账单 ZIP 通常包含两个文件:
    - ``账务明细.csv`` / ``zfb_trade_YYYYMMDD.csv``：交易明细（我们需要此文件）
    - ``汇总.csv``：汇总数据（跳过）

    CSV 关键列（以逗号分隔，部分字段头含空格）:
    ``支付宝交易号``, ``商户订单号``, ``交易状态``, ``金额（元）``, ``付款时间``

    失败时静默返回空列表。
    """
    results: List[ChannelSettlement] = []

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except zipfile.BadZipFile:
        logger.warning("alipay bill: not a valid ZIP file (date=%s)", date_str)
        return []

    detail_names = [
        n for n in zf.namelist()
        if not n.lower().startswith("汇总") and n.endswith(".csv")
    ]
    if not detail_names:
        detail_names = [n for n in zf.namelist() if n.endswith(".csv")]

    for name in detail_names:
        try:
            csv_bytes = zf.read(name)
            csv_text = csv_bytes.decode("gbk", errors="replace")
        except Exception as exc:  # noqa: BLE001
            logger.warning("alipay bill: failed to read %s: %s", name, exc)
            continue

        reader = csv.reader(io.StringIO(csv_text))
        header: List[str] = []
        for row in reader:
            if not row:
                continue
            stripped = [c.strip() for c in row]
            if not header:
                header = stripped
                continue
            if stripped[0].startswith("------") or stripped[0].startswith("合计") or stripped[0].startswith("总计"):
                continue
            record = dict(zip(header, stripped))
            trade_no = record.get("支付宝交易号", "").strip()
            out_trade_no = record.get("商户订单号", "").strip()
            trade_status = record.get("交易状态", "").strip()
            amount_raw = record.get("金额（元）", record.get("金额(元)", "0")).strip()
            time_raw = record.get("付款时间", record.get("创建时间", "")).strip()

            if not out_trade_no:
                continue

            try:
                amount_cents = int(round(float(amount_raw) * 100))
            except (TypeError, ValueError):
                amount_cents = 0

            try:
                settled_at = _dt.strptime(time_raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                settled_at = _dt.strptime(date_str, "%Y-%m-%d")

            if trade_status in ("交易成功", "TRADE_SUCCESS", "TRADE_FINISHED"):
                status = "paid"
            elif trade_status in ("交易关闭", "TRADE_CLOSED"):
                status = "closed"
            elif trade_status in ("退款成功",):
                status = "refunded"
            else:
                status = trade_status.lower() or "unknown"

            results.append(ChannelSettlement(
                provider="alipay",
                provider_trade_no=trade_no or out_trade_no,
                out_trade_no=out_trade_no,
                amount_cents=amount_cents,
                status=status,
                settled_at=settled_at,
                raw=record,
            ))

    logger.info("alipay fetch_settlements parsed %d rows for %s", len(results), date_str)
    return results


__all__ = ["AlipayGateway"]
