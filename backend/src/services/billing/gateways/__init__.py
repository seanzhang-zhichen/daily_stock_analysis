# -*- coding: utf-8 -*-
"""Payment gateway abstraction layer (Phase 5).

为微信支付 Native + 支付宝 PC 提供统一的入站抽象, 让 ``billing.py`` /
``OrderService`` 不再直接耦合通道 SDK 细节, 也方便单元测试 mock 通道行为。

子模块:

- :mod:`base`     抽象基类 / 数据结构 / 错误类型
- :mod:`wechat`   微信支付 Native (V3) gateway
- :mod:`alipay`   支付宝 PC 网站支付 gateway
- :mod:`factory`  从环境变量解析具体 gateway, 未配置或缺依赖时返回 ``None``

设计原则:

- **签名校验是 gateway 的核心职责**: 通过 :meth:`PaymentGateway.verify_callback`
  返回 :class:`CallbackResult`, 调用方根据 ``signature_valid`` 决定是否驱动业务。
- **下单 / 退款 / 拉账单**: 当前以接口形式声明, 真实 SDK 接入留待后续切片
  (W7 计划), 未接入时统一抛 :class:`NotImplementedError`, 调用方需要兜底。
- **可选依赖**: 加密相关依赖通过 ``cryptography`` 提供; 未安装时 factory 返回
  ``None`` (生产环境必须安装)。
"""

from src.services.billing.gateways.base import (
    CallbackResult,
    GatewayError,
    PaymentGateway,
)
from src.services.billing.gateways.factory import get_gateway, has_gateway

__all__ = [
    "CallbackResult",
    "GatewayError",
    "PaymentGateway",
    "get_gateway",
    "has_gateway",
]
