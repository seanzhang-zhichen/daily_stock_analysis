# -*- coding: utf-8 -*-
"""Payment gateway abstract base + 数据结构。

抽象层只暴露**对账意义**上的事件结构, 不暴露 SDK 细节, 方便:

- 单元测试 mock 整个 gateway, 不依赖真实证书 / 沙箱;
- 后续接入真实 SDK 时只改 gateway 实现, 不动 endpoint / OrderService。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, List, Optional


class GatewayError(Exception):
    """Gateway 内部错误的通用基类 (签名校验通过但业务驱动失败时上抛)。"""


@dataclass
class CallbackResult:
    """通道回调的规范化结果。

    所有字段在签名校验失败时仍允许填写 (用于落库审计), 但
    ``signature_valid=False`` 的事件**不应**驱动 ``fulfill_order``。

    Attributes:
        provider: ``wechat`` / ``alipay``。
        signature_valid: 通道签名是否通过。
        event_id: 通道事件唯一 ID, 用于 :class:`AppPaymentEvent` 幂等键。
        event_type: 规范化事件类型, 例如 ``pay.success`` / ``pay.fail`` /
            ``refund.success`` / ``callback.received``。
        out_trade_no: 商户订单号 (= :attr:`AppOrder.order_no`); 解析失败时为空。
        provider_trade_no: 通道交易号 (微信 transaction_id / 支付宝 trade_no)。
        amount_cents: 通道侧总金额 (分); 解析失败时为 ``0``。
        status: 规范化状态, 一般为 ``paid`` / ``refunded`` / ``closed`` /
            ``failed`` / ``unknown``。
        raw_payload: 原始 body (decoded), 用于落库审计 (脱敏后)。
        raw_event: 解密 / 解析后的事件字典, 仅供调试 / 落库。
        parse_error: 签名校验通过但 payload 解析失败时为 True。
    """

    provider: str
    signature_valid: bool
    event_id: Optional[str] = None
    event_type: str = "callback.received"
    out_trade_no: Optional[str] = None
    provider_trade_no: Optional[str] = None
    amount_cents: int = 0
    status: str = "unknown"
    raw_payload: str = ""
    raw_event: dict = field(default_factory=dict)
    parse_error: bool = False


@dataclass
class ChannelSettlement:
    """通道账单行 (规范化, 用于对账脚本)。

    与 :mod:`scripts.reconcile_payments` 中同名 dataclass 等价; 这里独立定义
    避免 gateway 反向依赖脚本。
    """

    provider: str
    provider_trade_no: str
    out_trade_no: str
    amount_cents: int
    status: str
    settled_at: datetime
    raw: dict = field(default_factory=dict)


class PaymentGateway:
    """支付通道统一接口 (Abstract)。

    子类按 provider 实现 :meth:`verify_callback`, 可选实现 :meth:`refund` /
    :meth:`fetch_settlements` / :meth:`place_order`。
    """

    provider: str = "abstract"

    # ── 回调校验 (必须实现) ────────────────────────────────────────────────

    def verify_callback(self, headers: dict, body: bytes) -> CallbackResult:
        """校验通道回调并规范化输出。

        Args:
            headers: HTTP header dict (大小写按通道实际处理)。
            body: 原始请求体 bytes。

        Returns:
            :class:`CallbackResult`, 包含 ``signature_valid`` + 规范化事件字段。

        实现要点 (各子类自行处理):

        - 时间戳偏差 (>5min) 视为签名失败;
        - 解析失败时仍要返回 :class:`CallbackResult` (signature_valid=False
          或 parse_error=True), 不抛异常 — 通道一般要求 200 响应。
        """
        raise NotImplementedError

    # ── 下单 / 退款 / 拉账单 (可选, 真实 SDK 接入时实现) ───────────────────

    def place_order(self, order, notify_url: Optional[str] = None) -> str:
        """向通道下单, 返回扫码用的 ``code_url`` (Native) 或 ``qr_url`` (Alipay)。

        参数:
            order: :class:`AppOrder` 实例 (避免 gateway 直接 import storage,
                这里用 ``Any`` 鸭子类型, 依赖 ``order_no`` / ``amount_cents``
                等字段)。
            notify_url: 异步通知地址, 默认从 env (``WECHAT_PAY_NOTIFY_URL`` /
                ``ALIPAY_NOTIFY_URL``) 取。

        默认实现抛 :class:`NotImplementedError`; 真实 SDK 接入后覆写。
        """
        raise NotImplementedError("place_order 尚未接入真实 SDK")

    def refund(
        self,
        out_trade_no: str,
        out_refund_no: str,
        amount_cents: int,
        total_cents: int,
        reason: Optional[str] = None,
    ) -> str:
        """向通道发起退款, 成功后返回 ``provider_refund_no``。

        失败时抛 :class:`GatewayError`; SDK 未接入时抛 :class:`NotImplementedError`,
        调用方应据此回退到「人工填写 provider_refund_no」路径。
        """
        raise NotImplementedError("refund 尚未接入真实 SDK")

    def fetch_settlements(self, target_date: date) -> List[ChannelSettlement]:
        """拉取目标日的通道账单, 用于对账。

        默认返回空列表 + warning 日志, 不抛异常, 方便对账脚本骨架期可跑。
        """
        return []


__all__ = [
    "CallbackResult",
    "ChannelSettlement",
    "GatewayError",
    "PaymentGateway",
]
