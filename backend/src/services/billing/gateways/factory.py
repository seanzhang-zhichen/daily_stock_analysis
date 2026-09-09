# -*- coding: utf-8 -*-
"""支付网关工厂：根据环境变量与平台设置构造具体的 :class:`PaymentGateway` 实例。

设计要点：

- **关闭优先**：`PAYMENT_ENABLED=false`（平台设置 / 环境变量）时永远返回 ``None``，
  即使密钥已经配置好，便于在不需要支付能力的环境（如开发/演示）中关闭通道。
- **缺一不可**：关键凭据任一缺失即返回 ``None``；调用方必须容错（落库 + 503 提示等）。
- **测试友好**：可通过 :func:`set_gateway_override` 注入 mock gateway，优先级最高，
  避免污染真实环境。

对外暴露的入口函数是 :func:`get_gateway` 与 :func:`has_gateway`。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy.orm import Session

from src.services.billing.gateways.base import PaymentGateway
from src.users.platform_settings import get_platform_setting_value

logger = logging.getLogger(__name__)


# 测试 / 沙箱注入: provider -> PaymentGateway 实例
_OVERRIDES: Dict[str, PaymentGateway] = {}


def set_gateway_override(provider: str, gateway: Optional[PaymentGateway]) -> None:
    """供测试注入 mock gateway；传 ``None`` 时清除对应 provider 的 override。

    Args:
        provider: 支付渠道标识，如 ``wechat`` / ``alipay``。
        gateway: 要注入的网关实例；为 ``None`` 表示清除已有覆盖。
    """
    if gateway is None:
        _OVERRIDES.pop(provider, None)
    else:
        _OVERRIDES[provider] = gateway


def clear_gateway_overrides() -> None:
    """清空所有测试 / 沙箱注入的 mock gateway 覆盖。"""
    _OVERRIDES.clear()


def _flag(name: str) -> bool:
    """读取布尔形态的环境变量标志（1 / true / yes 视为开启，其余视为关闭）。"""
    return os.environ.get(name, "false").lower() in ("1", "true", "yes")


def _read_pem_or_path(env_name_pem: str, env_name_path: str) -> Optional[str]:
    """优先读取 PEM 内容环境变量，没有时再读取文件路径，便于 Docker secret / 本地测试两种部署场景。

    Args:
        env_name_pem: 直接存放 PEM 内容的环境变量名。
        env_name_path: 指向 PEM 文件路径的环境变量名。

    Returns:
        Optional[str]: PEM 内容；两者均未配置或读取失败时返回 ``None``。
    """
    pem = (os.environ.get(env_name_pem) or "").strip()
    if pem:
        return pem
    path_raw = (os.environ.get(env_name_path) or "").strip()
    if not path_raw:
        return None
    try:
        return Path(path_raw).read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("读取 %s 文件失败 (%s): %s", env_name_path, path_raw, exc)
        return None


def _build_wechat() -> Optional[PaymentGateway]:
    """构造微信支付网关；任一关键凭据缺失时返回 ``None``。

    关键凭据：``app_id`` / ``mch_id`` / ``apiv3_key`` / 平台证书 PEM 内容。
    商户私钥、回调地址、可选证书序列号为非必需项，未配置时传入空串或 None。
    """
    app_id = (os.environ.get("WECHAT_PAY_APP_ID") or "").strip()
    mch_id = (os.environ.get("WECHAT_PAY_MCH_ID") or "").strip()
    apiv3_key = (os.environ.get("WECHAT_PAY_APIV3_KEY") or "").strip()
    cert_pem = _read_pem_or_path(
        "WECHAT_PAY_PLATFORM_CERT_PEM",
        "WECHAT_PAY_PLATFORM_CERT_PATH",
    )
    merchant_private_key_pem = _read_pem_or_path(
        "WECHAT_PAY_PRIVATE_KEY_PEM",
        "WECHAT_PAY_PRIVATE_KEY_PATH",
    )
    cert_serial_no = (os.environ.get("WECHAT_PAY_CERT_SERIAL_NO") or "").strip()

    # 核心凭据任一缺失即视为未配置支付
    if not (app_id and mch_id and apiv3_key and cert_pem):
        return None

    try:
        from src.services.billing.gateways.wechat import WechatGateway
    except ImportError as exc:  # cryptography 缺失等
        logger.warning("WechatGateway 加载失败: %s", exc)
        return None

    return WechatGateway(
        app_id=app_id,
        mch_id=mch_id,
        apiv3_key=apiv3_key,
        platform_cert_pem=cert_pem,
        cert_serial_no=cert_serial_no,
        merchant_private_key_pem=merchant_private_key_pem or "",
        notify_url=(os.environ.get("WECHAT_PAY_NOTIFY_URL") or "").strip() or None,
    )


def _build_alipay() -> Optional[PaymentGateway]:
    """构造支付宝网关；任一关键凭据缺失时返回 ``None``。

    关键凭据：``app_id`` / 支付宝公钥 PEM 内容；
    应用私钥、回调 / 返回地址为可选项。
    """
    app_id = (os.environ.get("ALIPAY_APP_ID") or "").strip()
    pubkey_pem = _read_pem_or_path(
        "ALIPAY_PUBLIC_KEY_PEM",
        "ALIPAY_PUBLIC_KEY_PATH",
    )
    app_private_key_pem = _read_pem_or_path(
        "ALIPAY_APP_PRIVATE_KEY_PEM",
        "ALIPAY_APP_PRIVATE_KEY_PATH",
    )

    # app_id 与公钥是验签 / 通信必需
    if not (app_id and pubkey_pem):
        return None

    try:
        from src.services.billing.gateways.alipay import AlipayGateway
    except ImportError as exc:
        logger.warning("AlipayGateway 加载失败: %s", exc)
        return None

    return AlipayGateway(
        app_id=app_id,
        alipay_public_key_pem=pubkey_pem,
        app_private_key_pem=app_private_key_pem or "",
        notify_url=(os.environ.get("ALIPAY_NOTIFY_URL") or "").strip() or None,
        return_url=(os.environ.get("ALIPAY_RETURN_URL") or "").strip() or None,
    )


def get_gateway(provider: str, db: Optional[Session] = None) -> Optional[PaymentGateway]:
    """根据 provider 返回当前可用的支付网关实例；未配置或 PAYMENT_ENABLED=false 时返回 ``None``。

    Args:
        provider: 支付渠道标识，目前支持 ``wechat`` / ``alipay``。
        db: 可选数据库会话，用于读取平台级设置 ``PAYMENT_ENABLED``。

    Returns:
        Optional[PaymentGateway]: 可用网关实例；不可用时返回 ``None``。
    """
    # 测试注入的 mock 优先级最高（避免打真实支付）
    if provider in _OVERRIDES:
        return _OVERRIDES[provider]

    # 平台关闭支付时直接短路
    if not bool(get_platform_setting_value(db, "PAYMENT_ENABLED")):
        return None

    # 按 provider 路由到具体网关构造器
    if provider == "wechat":
        return _build_wechat()
    if provider == "alipay":
        return _build_alipay()
    return None


def has_gateway(provider: str) -> bool:
    """判断指定 provider 的网关当前是否可用（已配置且未被关闭）。"""
    return get_gateway(provider) is not None


__all__ = [
    "get_gateway",
    "has_gateway",
    "set_gateway_override",
    "clear_gateway_overrides",
]
