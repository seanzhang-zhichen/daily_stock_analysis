# -*- coding: utf-8 -*-
"""回调安全工具 (Phase 5 安全收尾)。

提供两个能力:

1. **IP 白名单检查** (:func:`check_callback_ip`)
   - 通过 ``PAYMENT_CALLBACK_ALLOWED_IPS`` 配置允许的 CIDR / IP 列表（逗号分隔）。
   - 留空时不做限制（允许所有来源），适合初期或不经过固定 IP 的场景。
   - 命中黑名单时返回 ``False``；调用方应立即返回 HTTP 200 但不驱动业务，
     避免触发通道重试风暴（DDoS 防护）。

2. **签名失败滑动窗口告警** (:func:`record_sig_failure`)
   - 在内存中维护 per-provider 的失败时间戳队列（滑动窗口）。
   - 失败次数在 ``PAYMENT_CALLBACK_SIG_FAIL_WINDOW_SECONDS``（默认 300s）内
     达到 ``PAYMENT_CALLBACK_SIG_FAIL_THRESHOLD``（默认 5 次）时触发告警。
   - 告警通过 ``ADMIN_ALERT_EMAIL`` 邮件 + ``RECONCILE_WEBHOOK_URL`` Webhook 双路发出。
   - 内置 30 分钟冷却期（``_ALERT_COOLDOWN_SECONDS``）防止告警风暴。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import threading
import time
import urllib.request
from collections import deque
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── 告警冷却 ────────────────────────────────────────────────────────────────

_ALERT_COOLDOWN_SECONDS = 1800  # 30 分钟内不重复发同一 provider 的告警

_lock = threading.Lock()
_sig_fail_windows: dict = {}   # provider -> deque[float]  (monotonic timestamps)
_last_alert_times: dict = {}   # provider -> float  (monotonic)


# ── IP 白名单 ────────────────────────────────────────────────────────────────

def _parse_networks(raw: str) -> Optional[List[ipaddress.IPv4Network]]:
    """解析逗号分隔的 CIDR / IP 字符串，返回网络列表；空字符串返回 ``None``（不限制）。"""
    raw = raw.strip()
    if not raw:
        return None
    nets: List = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("PAYMENT_CALLBACK_ALLOWED_IPS 中存在无效 CIDR: %r，已跳过", part)
    return nets if nets else None


def _client_ip(request) -> Optional[str]:
    """从 FastAPI Request 中提取真实客户端 IP（兼容 Nginx X-Forwarded-For / X-Real-IP）。"""
    for hdr in ("x-real-ip", "x-forwarded-for"):
        val = (request.headers.get(hdr) or "").split(",")[0].strip()
        if val:
            return val
    client = getattr(request, "client", None)
    return client.host if client else None


def check_callback_ip(request, provider: str) -> bool:
    """检查回调请求的来源 IP 是否在白名单内。

    Args:
        request: FastAPI ``Request`` 对象。
        provider: ``wechat`` / ``alipay``，用于日志和 per-provider 配置扩展。

    Returns:
        ``True`` 允许继续处理；``False`` 表示 IP 不在白名单，调用方应丢弃（返回 200）。

    配置规则（优先级从高到低）:

    - ``PAYMENT_CALLBACK_ALLOWED_IPS_{PROVIDER}``（如 ``PAYMENT_CALLBACK_ALLOWED_IPS_WECHAT``）
    - ``PAYMENT_CALLBACK_ALLOWED_IPS``（全局配置）
    - 两者均未设置 → 允许所有 IP。
    """
    per_key = f"PAYMENT_CALLBACK_ALLOWED_IPS_{provider.upper()}"
    per_val = os.environ.get(per_key)
    if per_val is not None:
        networks = _parse_networks(per_val)
    else:
        networks = _parse_networks(os.environ.get("PAYMENT_CALLBACK_ALLOWED_IPS", ""))

    if networks is None:
        return True  # 未配置，允许所有

    ip_str = _client_ip(request)
    if not ip_str:
        logger.warning("callback IP 检查: 无法获取客户端 IP (provider=%s)，放行但记录", provider)
        return True

    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        logger.warning("callback IP 检查: IP 格式无效 %r (provider=%s)，放行但记录", ip_str, provider)
        return True

    if any(addr in net for net in networks):
        return True

    logger.warning(
        "callback IP 不在白名单: ip=%s provider=%s — 已拦截，返回 200 避免重试",
        ip_str, provider,
    )
    return False


# ── 签名失败滑动窗口告警 ─────────────────────────────────────────────────────

def record_sig_failure(provider: str) -> None:
    """记录一次签名失败，窗口内超过阈值时触发告警。

    完全在内存中进行（不依赖 DB），线程安全。
    告警发出后进入 :const:`_ALERT_COOLDOWN_SECONDS` 冷却期。

    Args:
        provider: ``wechat`` / ``alipay``。
    """
    threshold = int(os.environ.get("PAYMENT_CALLBACK_SIG_FAIL_THRESHOLD", "5"))
    window_secs = int(os.environ.get("PAYMENT_CALLBACK_SIG_FAIL_WINDOW_SECONDS", "300"))
    now = time.monotonic()

    with _lock:
        if provider not in _sig_fail_windows:
            _sig_fail_windows[provider] = deque()
        dq: deque = _sig_fail_windows[provider]
        dq.append(now)
        cutoff = now - window_secs
        while dq and dq[0] < cutoff:
            dq.popleft()
        count = len(dq)
        last_alert = _last_alert_times.get(provider, 0.0)
        should_alert = count >= threshold and (now - last_alert) > _ALERT_COOLDOWN_SECONDS
        if should_alert:
            _last_alert_times[provider] = now

    if should_alert:
        _emit_sig_fail_alert(provider, count, window_secs)
    else:
        logger.debug(
            "sig_failure recorded: provider=%s count_in_window=%d threshold=%d",
            provider, count, threshold,
        )


def _emit_sig_fail_alert(provider: str, count: int, window_secs: int) -> None:
    subject = f"[DSA] 回调签名失败告警 provider={provider}"
    body = (
        f"[回调签名告警]\n"
        f"  provider    : {provider}\n"
        f"  失败次数    : {count} 次（近 {window_secs}s 内）\n"
        f"  可能原因    : 伪造攻击、证书轮换后配置未更新、中间人篡改\n"
        f"  建议操作    : 检查 app_payment_events 表中 signature_valid=false 记录；"
        f"如为正常证书轮换请更新 WECHAT_PAY_PLATFORM_CERT_PEM / ALIPAY_PUBLIC_KEY_PEM 后重启。\n"
    )
    logger.warning(body.replace("\n", " | "))
    _notify_admin(subject, body)


def _notify_admin(subject: str, body: str) -> None:
    """通过邮件（ADMIN_ALERT_EMAIL）和 Webhook（RECONCILE_WEBHOOK_URL）发送告警。"""
    alert_email = (os.environ.get("ADMIN_ALERT_EMAIL") or "").strip()
    if alert_email:
        try:
            from src.users.email import EmailMessageDTO, get_email_backend
            backend = get_email_backend()
            backend.send(EmailMessageDTO(to=alert_email, subject=subject, body_text=body))
            logger.info("签名失败告警邮件已发送至 %s", alert_email)
        except Exception:  # noqa: BLE001
            logger.warning("签名失败告警邮件发送失败", exc_info=True)

    webhook_url = (os.environ.get("RECONCILE_WEBHOOK_URL") or "").strip()
    if webhook_url:
        payload = json.dumps(
            {"msg_type": "text", "content": {"text": f"{subject}\n{body}"}},
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info("签名失败告警 Webhook 发送成功 status=%s", resp.status)
        except Exception:  # noqa: BLE001
            logger.warning("签名失败告警 Webhook 发送失败 url=%s", webhook_url, exc_info=True)


__all__ = [
    "check_callback_ip",
    "record_sig_failure",
]
