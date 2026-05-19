# -*- coding: utf-8 -*-
"""邮件发送抽象。

MVP 阶段实现两个后端:

- :class:`LoggingEmailBackend`  开发/默认: 把邮件内容写到日志, 不真正发件。
- :class:`SmtpEmailBackend`     生产: 使用 ``EMAIL_SENDER`` / ``EMAIL_PASSWORD``
  等已存在的环境变量直接 SMTP 发送。

后续可按需要扩展 SES / 阿里云邮件推送等。
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessageDTO:
    to: str
    subject: str
    body_text: str
    body_html: str | None = None


class EmailBackend(Protocol):
    def send(self, message: EmailMessageDTO) -> None:  # noqa: D401
        """发送邮件, 失败时抛出异常。"""


class LoggingEmailBackend:
    """默认实现: 不真正发件, 仅写日志。"""

    def send(self, message: EmailMessageDTO) -> None:
        logger.warning(
            "[email-stub] to=%s subject=%s\n%s",
            message.to,
            message.subject,
            message.body_text,
        )


class SmtpEmailBackend:
    """复用项目已有的 ``EMAIL_SENDER`` / ``EMAIL_PASSWORD`` / ``SMTP_*`` 配置发件。"""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        use_tls: bool = True,
        use_ssl: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._use_tls = use_tls
        self._use_ssl = use_ssl

    def send(self, message: EmailMessageDTO) -> None:
        msg = EmailMessage()
        msg["Subject"] = message.subject
        msg["From"] = self._sender
        msg["To"] = message.to
        msg.set_content(message.body_text)
        if message.body_html:
            msg.add_alternative(message.body_html, subtype="html")

        if self._use_ssl:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=15) as smtp:
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(self._host, self._port, timeout=15) as smtp:
                if self._use_tls:
                    smtp.starttls()
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                smtp.send_message(msg)


def _coerce_int(value: str | None, default: int) -> int:
    try:
        return int((value or "").strip() or default)
    except ValueError:
        return default


_SMTP_CONFIGS: dict[str, dict] = {
    "qq.com":      {"host": "smtp.qq.com",             "port": 465, "use_tls": False, "use_ssl": True},
    "foxmail.com": {"host": "smtp.qq.com",             "port": 465, "use_tls": False, "use_ssl": True},
    "163.com":     {"host": "smtp.163.com",            "port": 465, "use_tls": False, "use_ssl": True},
    "126.com":     {"host": "smtp.126.com",            "port": 465, "use_tls": False, "use_ssl": True},
    "gmail.com":   {"host": "smtp.gmail.com",          "port": 587, "use_tls": True,  "use_ssl": False},
    "outlook.com": {"host": "smtp-mail.outlook.com",   "port": 587, "use_tls": True,  "use_ssl": False},
    "hotmail.com": {"host": "smtp-mail.outlook.com",   "port": 587, "use_tls": True,  "use_ssl": False},
    "live.com":    {"host": "smtp-mail.outlook.com",   "port": 587, "use_tls": True,  "use_ssl": False},
    "sina.com":    {"host": "smtp.sina.com",           "port": 465, "use_tls": False, "use_ssl": True},
    "sohu.com":    {"host": "smtp.sohu.com",           "port": 465, "use_tls": False, "use_ssl": True},
    "aliyun.com":  {"host": "smtp.aliyun.com",         "port": 465, "use_tls": False, "use_ssl": True},
    "139.com":     {"host": "smtp.139.com",            "port": 465, "use_tls": False, "use_ssl": True},
}


def _detect_smtp_from_sender(sender: str) -> dict | None:
    """从发件人邮箱域名推断 SMTP 配置，与通知系统保持一致。"""
    if "@" not in sender:
        return None
    domain = sender.split("@", 1)[1].lower()
    return _SMTP_CONFIGS.get(domain)


def get_email_backend() -> EmailBackend:
    """根据环境变量返回当前可用的邮件后端。

    优先级：
    1. ``USER_EMAIL_BACKEND=smtp`` 且 ``SMTP_HOST`` / ``EMAIL_SENDER`` 完整 → 使用显式配置
    2. ``EMAIL_SENDER`` + ``EMAIL_PASSWORD`` 已配置 → 自动从域名推断 SMTP Host（与通知系统一致）
    3. 其余情况 → 退化为日志后端（开发环境无需任何配置）
    """
    sender = (os.getenv("EMAIL_SENDER") or os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("EMAIL_PASSWORD") or os.getenv("SMTP_PASSWORD") or "").strip()

    backend_kind = (os.getenv("USER_EMAIL_BACKEND") or "").strip().lower()
    if backend_kind == "smtp":
        host = (os.getenv("SMTP_HOST") or os.getenv("EMAIL_SMTP_HOST") or "").strip()
        if not host or not sender:
            logger.warning("USER_EMAIL_BACKEND=smtp 但缺少 SMTP_HOST / EMAIL_SENDER, 回退为日志后端")
            return LoggingEmailBackend()
        port = _coerce_int(os.getenv("SMTP_PORT") or os.getenv("EMAIL_SMTP_PORT"), 587)
        use_tls = (os.getenv("SMTP_USE_TLS") or "true").strip().lower() not in ("0", "false", "no", "off")
        username = (os.getenv("SMTP_USERNAME") or sender).strip()
        return SmtpEmailBackend(host=host, port=port, username=username, password=password, sender=sender, use_tls=use_tls)

    if sender and password:
        detected = _detect_smtp_from_sender(sender)
        if detected:
            return SmtpEmailBackend(
                host=detected["host"],
                port=detected["port"],
                username=sender,
                password=password,
                sender=sender,
                use_tls=detected["use_tls"],
                use_ssl=detected["use_ssl"],
            )
        host = (os.getenv("SMTP_HOST") or os.getenv("EMAIL_SMTP_HOST") or "").strip()
        if host:
            port = _coerce_int(os.getenv("SMTP_PORT") or os.getenv("EMAIL_SMTP_PORT"), 587)
            use_tls = (os.getenv("SMTP_USE_TLS") or "true").strip().lower() not in ("0", "false", "no", "off")
            username = (os.getenv("SMTP_USERNAME") or sender).strip()
            return SmtpEmailBackend(host=host, port=port, username=username, password=password, sender=sender, use_tls=use_tls)
        logger.warning("[email] EMAIL_SENDER 已配置但无法推断 SMTP_HOST, 回退为日志后端（请设置 SMTP_HOST）")

    return LoggingEmailBackend()
