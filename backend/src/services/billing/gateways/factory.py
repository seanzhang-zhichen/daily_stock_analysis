# -*- coding: utf-8 -*-
"""Gateway 工厂 — 从环境变量解析具体 :class:`PaymentGateway` 实例。

约定:

- ``PAYMENT_ENABLED=false`` 时永远返回 ``None`` (即使密钥已配置)。
- 关键密钥任一缺失即返回 ``None``, 调用方必须容错 (callback 落库 + 503 提示等)。
- 测试可通过 :func:`set_gateway_override` 注入 mock gateway, 优先级最高。

调用入口: :func:`get_gateway` / :func:`has_gateway`。
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
    """供测试注入 mock gateway; 传 ``None`` 时清除 override。"""
    if gateway is None:
        _OVERRIDES.pop(provider, None)
    else:
        _OVERRIDES[provider] = gateway


def clear_gateway_overrides() -> None:
    _OVERRIDES.clear()


def _flag(name: str) -> bool:
    return os.environ.get(name, "false").lower() in ("1", "true", "yes")


def _read_pem_or_path(env_name_pem: str, env_name_path: str) -> Optional[str]:
    """优先读 PEM 内容 (env), 没有就读文件路径。便于 docker secret / 测试两种场景。"""
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
    app_id = (os.environ.get("ALIPAY_APP_ID") or "").strip()
    pubkey_pem = _read_pem_or_path(
        "ALIPAY_PUBLIC_KEY_PEM",
        "ALIPAY_PUBLIC_KEY_PATH",
    )
    app_private_key_pem = _read_pem_or_path(
        "ALIPAY_APP_PRIVATE_KEY_PEM",
        "ALIPAY_APP_PRIVATE_KEY_PATH",
    )

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
    """根据 provider 返回 gateway 实例; 未配置或 PAYMENT_ENABLED=false 时返回 None。

    Args:
        provider: ``wechat`` 或 ``alipay``。
    """
    if provider in _OVERRIDES:
        return _OVERRIDES[provider]

    if not bool(get_platform_setting_value(db, "PAYMENT_ENABLED")):
        return None

    if provider == "wechat":
        return _build_wechat()
    if provider == "alipay":
        return _build_alipay()
    return None


def has_gateway(provider: str) -> bool:
    return get_gateway(provider) is not None


__all__ = [
    "get_gateway",
    "has_gateway",
    "set_gateway_override",
    "clear_gateway_overrides",
]
