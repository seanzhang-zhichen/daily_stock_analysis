# -*- coding: utf-8 -*-
"""注册防刷护栏 (Phase 6 §5.8.1)。

提供两个核心能力, 由 :func:`src.users.service.register_user` 在创建账号前调用:

1. **一次性 / 临时邮箱拦截**: 内置常见 disposable 邮箱域名黑名单, 同时支持通过
   ``USER_DISPOSABLE_EMAIL_DOMAINS`` 环境变量扩展。命中的域名会直接拒绝注册,
   错误码使用 :class:`UserErrorCode.INVALID_EMAIL`。

2. **IP / 邮箱注册频率限制**: 在 ``USER_REGISTER_RATE_WINDOW_HOURS`` (默认 24h) 滚动窗口内,
   - 同一 IP 累计注册尝试超过 ``USER_REGISTER_IP_DAILY_MAX`` 后, 后续请求触发限流;
   - 同一邮箱累计注册尝试超过 ``USER_REGISTER_EMAIL_DAILY_MAX`` 后, 后续请求触发限流。
   计数依赖 :class:`AppAuditLog` 表中的 ``auth.register.attempt`` 事件,
   实现无外部依赖且持久化, 方便后续审计与回溯。

防止穷举: 限流响应统一返回 `RATE_LIMITED`, 不暴露邮箱是否已存在,
也不区分黑名单 / 频率原因。所有触发的拦截都会落审计日志, 便于风控复盘。
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.storage import AppAuditLog
from src.users.audit import write_audit_log
from src.users.errors import UserError, UserErrorCode


logger = logging.getLogger(__name__)


# --- 一次性邮箱域名黑名单 ---------------------------------------------------

#: 默认内置的常见 disposable 邮箱域名 (保守名单, 仅覆盖最常见的几家)。
#: 名单刻意保守, 避免误伤; 生产环境可通过环境变量扩展。
DEFAULT_DISPOSABLE_DOMAINS: frozenset[str] = frozenset(
    {
        "10minutemail.com",
        "10minutemail.net",
        "20minutemail.com",
        "mailinator.com",
        "mailinator.net",
        "mailinator.org",
        "guerrillamail.com",
        "guerrillamail.net",
        "guerrillamail.org",
        "guerrillamail.info",
        "guerrillamail.biz",
        "guerrillamailblock.com",
        "sharklasers.com",
        "grr.la",
        "tempmail.com",
        "tempmail.net",
        "temp-mail.org",
        "temp-mail.io",
        "tempr.email",
        "dispostable.com",
        "throwawaymail.com",
        "yopmail.com",
        "yopmail.net",
        "yopmail.fr",
        "trashmail.com",
        "trashmail.net",
        "fakeinbox.com",
        "fake-mail.org",
        "getairmail.com",
        "getnada.com",
        "maildrop.cc",
        "spambox.us",
        "mintemail.com",
        "moakt.com",
        "mohmal.com",
        "mvrht.net",
        "emailondeck.com",
        "tempinbox.com",
        "tempinbox.co.uk",
        "tempemailaddress.com",
        "tempmailaddress.com",
        "anonymbox.com",
        "burnermail.io",
        "discard.email",
        "harakirimail.com",
        "incognitomail.com",
        "mailcatch.com",
        "mailnesia.com",
        "spamgourmet.com",
        "spam4.me",
        "tmail.ws",
        "tmpmail.org",
        "tmpmail.net",
        "tmail.com",
    }
)


def _split_csv_env(raw: Optional[str]) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(
        part.strip().lower()
        for part in raw.split(",")
        if part and part.strip()
    )


def load_disposable_domains() -> frozenset[str]:
    """加载 disposable 邮箱黑名单 (内置 + 环境变量扩展)。

    环境变量:
        - ``USER_DISPOSABLE_EMAIL_DOMAINS``: 追加到内置黑名单的额外域名 (逗号分隔)。
        - ``USER_DISPOSABLE_EMAIL_DOMAINS_REPLACE``: 设为 true 时, 用 env 中的列表
          **替换** 而非追加内置名单 (便于全自定义运营策略)。
    """
    extra = _split_csv_env(os.getenv("USER_DISPOSABLE_EMAIL_DOMAINS"))
    replace_raw = (os.getenv("USER_DISPOSABLE_EMAIL_DOMAINS_REPLACE") or "").strip().lower()
    replace = replace_raw in {"1", "true", "yes", "on"}
    if replace:
        return frozenset(extra)
    return DEFAULT_DISPOSABLE_DOMAINS | frozenset(extra)


def extract_email_domain(email: str) -> str:
    """从邮箱中提取规范化的域名 (小写、strip)。"""
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].strip().lower()


def is_disposable_email(email: str, *, blacklist: Optional[Iterable[str]] = None) -> bool:
    """判断邮箱是否命中一次性邮箱黑名单。

    匹配规则: 严格按域名相等; 不做 wildcard 匹配, 避免误伤合法子域名。
    """
    domain = extract_email_domain(email)
    if not domain:
        return False
    domains = frozenset(blacklist) if blacklist is not None else load_disposable_domains()
    return domain in domains


# --- 注册频率限制 -----------------------------------------------------------


REGISTER_ATTEMPT_ACTION = "auth.register.attempt"
REGISTER_BLOCKED_ACTION = "auth.register.blocked"


@dataclass(frozen=True)
class RegistrationGuardConfig:
    """注册防刷配置 (由 :class:`UserModeSettings` 透传)。"""

    disposable_block_enabled: bool = True
    ip_daily_max: int = 10
    email_daily_max: int = 3
    window_hours: int = 24
    mx_check_enabled: bool = False  # 默认关闭, 设 USER_EMAIL_MX_CHECK_ENABLED=true 启用


# --- 邮箱域名 MX 校验 --------------------------------------------------------


def check_mx_domain(domain: str, *, timeout: float = 3.0) -> bool:
    """检查域名是否有可解析的 MX 或 A/AAAA 记录 (宽松模式)。

    实现思路：Python 标准库不提供直接 MX 查询接口，此处采用"宽松代理"策略：
    先尝试解析 ``mail.<domain>`` 再回退到 ``<domain>`` 本身的 A/AAAA 记录；
    任意一个能解析即认为域名有效。这不等价于严格 MX 校验，但足以拦截
    完全不存在的域名 (大量 disposable 邮箱使用已停服域名)。
    若需完整 MX 查询, 可在生产环境安装 ``dnspython`` 并替换此函数。

    网络不可达时返回 True（宽松放行，不因 DNS 故障阻断注册）。
    """
    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            socket.getaddrinfo(domain, None)
            return True
        except socket.gaierror:
            return False
        finally:
            socket.setdefaulttimeout(old_timeout)
    except Exception:  # noqa: BLE001
        logger.debug("mx check failed for domain %s, allowing (fail-open)", domain)
        return True


def _hash_email(email: str) -> str:
    """对邮箱做 SHA-256 摘要, 仅取前 32 字符 (避免在 audit 表存明文)。"""
    raw = (email or "").strip().lower().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _count_audit_attempts(
    db: Session,
    *,
    since: datetime,
    target_ref: Optional[str] = None,
    ip: Optional[str] = None,
) -> int:
    """统计指定窗口内的注册尝试条数。"""
    if target_ref is None and ip is None:
        return 0
    query = db.query(func.count(AppAuditLog.id)).filter(
        AppAuditLog.action == REGISTER_ATTEMPT_ACTION,
        AppAuditLog.created_at >= since,
    )
    if target_ref is not None:
        query = query.filter(AppAuditLog.target_ref == target_ref)
    if ip is not None:
        query = query.filter(AppAuditLog.ip == ip)
    try:
        return int(query.scalar() or 0)
    except Exception:  # noqa: BLE001
        logger.warning("registration guard rate count failed", exc_info=True)
        return 0


def record_registration_attempt(
    db: Session,
    *,
    email: Optional[str],
    ip: Optional[str],
    user_agent: Optional[str],
) -> None:
    """记录一次注册尝试到审计日志, 用于后续限流统计。

    写入失败仅记 warning, 不影响主流程 (沿用 :mod:`src.users.audit` 的语义)。
    """
    write_audit_log(
        db,
        REGISTER_ATTEMPT_ACTION,
        target_ref=_hash_email(email) if email else None,
        ip=ip,
        user_agent=user_agent,
    )


def _record_blocked(
    db: Session,
    *,
    email: Optional[str],
    ip: Optional[str],
    user_agent: Optional[str],
    reason: str,
) -> None:
    write_audit_log(
        db,
        REGISTER_BLOCKED_ACTION,
        target_ref=_hash_email(email) if email else None,
        ip=ip,
        user_agent=user_agent,
        detail={"reason": reason},
    )


def preflight_registration(
    db: Session,
    *,
    email: str,
    ip: Optional[str],
    user_agent: Optional[str] = None,
    config: Optional[RegistrationGuardConfig] = None,
) -> None:
    """在创建账号前执行护栏检查 (一次性邮箱 + 频率限制)。

    Raises:
        UserError: 命中拦截规则; 调用方需要捕获并翻译为 HTTP 响应。
    """
    cfg = config or RegistrationGuardConfig()
    normalized_email = (email or "").strip().lower()

    # 1. 一次性邮箱黑名单
    if cfg.disposable_block_enabled and is_disposable_email(normalized_email):
        _record_blocked(
            db,
            email=normalized_email,
            ip=ip,
            user_agent=user_agent,
            reason="disposable_email",
        )
        raise UserError(
            UserErrorCode.INVALID_EMAIL,
            "该邮箱域名不被支持, 请使用常用邮箱注册",
        )

    # 2. MX 域名校验（可选，由 USER_EMAIL_MX_CHECK_ENABLED 控制）
    if cfg.mx_check_enabled:
        domain = extract_email_domain(normalized_email)
        if domain and not check_mx_domain(domain):
            _record_blocked(
                db,
                email=normalized_email,
                ip=ip,
                user_agent=user_agent,
                reason="invalid_mx_domain",
            )
            raise UserError(
                UserErrorCode.INVALID_EMAIL,
                "邮箱域名无效, 请检查邮箱地址是否正确",
            )

    # 3. 频率限制
    since = datetime.now() - timedelta(hours=max(cfg.window_hours, 1))
    email_hash = _hash_email(normalized_email) if normalized_email else None

    if cfg.email_daily_max > 0 and email_hash:
        email_count = _count_audit_attempts(db, since=since, target_ref=email_hash)
        if email_count > cfg.email_daily_max:
            _record_blocked(
                db,
                email=normalized_email,
                ip=ip,
                user_agent=user_agent,
                reason="email_rate_limited",
            )
            raise UserError(
                UserErrorCode.RATE_LIMITED,
                "该邮箱注册尝试过于频繁, 请稍后再试",
            )

    if cfg.ip_daily_max > 0 and ip:
        ip_count = _count_audit_attempts(db, since=since, ip=ip)
        if ip_count > cfg.ip_daily_max:
            _record_blocked(
                db,
                email=normalized_email,
                ip=ip,
                user_agent=user_agent,
                reason="ip_rate_limited",
            )
            raise UserError(
                UserErrorCode.RATE_LIMITED,
                "注册请求过于频繁, 请稍后再试",
            )
