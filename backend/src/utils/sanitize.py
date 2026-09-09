# -*- coding: utf-8 -*-
"""日志、诊断信息与 API payload 共享的文本脱敏工具。

统一负责把 Authorization / Cookie / Token / Webhook / 各类凭据类字符串从
诊断输出、决策信号文本与持久化 payload 中清洗掉，避免泄漏到日志、数据库或前端响应。
供日志模块、决策信号序列化模块、API 响应组装模块复用。

主要能力：
- 文本/字符串中的密钥与 URL 脱敏（sanitize_diagnostic_text / sanitize_decision_signal_text）
- 字典/列表结构按敏感键递归脱敏（redact_sensitive_mapping / sanitize_decision_signal_payload）
- 针对常见 Webhook 域名与 token 形态的内置启发式识别
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit


_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "sendkey",
    "token",
    "webhook",
}
_SENSITIVE_KEY_PHRASES = {
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "api_token",
    "apitoken",
    "auth_token",
    "authtoken",
    "authorization_header",
    "authorizationheader",
    "license_key",
    "licensekey",
    "private_key",
    "privatekey",
    "refresh_token",
    "refreshtoken",
    "secret_key",
    "secretkey",
    "session_token",
    "sessiontoken",
    "send_key",
    "sendkey",
    "webhook_url",
    "webhookurl",
}
_SENSITIVE_COMPACT_KEY_PHRASES = {
    phrase.replace("_", "") for phrase in _SENSITIVE_KEY_PHRASES
}
_SENSITIVE_COMPACT_KEY_PATTERN = re.compile(
    r"authorization|cookie|password|secret|sendkey|token(?!s)|webhook"
)
_URL_PATTERN = re.compile(r"https?://[^\s,;)\]}]+", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"\b(bearer\s+)[^\s,;&]+", re.IGNORECASE)
_AUTHORIZATION_HEADER_PATTERN = re.compile(
    r"\b(authorization|proxy[_-]?authorization)(\s*[:=]\s*)"
    r"(?:(?:Bearer|Basic|Token|Digest)\s+)?[^\s,;&]+",
    re.IGNORECASE,
)
_COOKIE_HEADER_PATTERN = re.compile(
    r"\b(cookie|set[_-]?cookie)(\s*[:=]\s*)[^\s,;&]+",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b(token|secret|password|sendkey|api[_-]?key|apikey|api[_-]?token|auth[_-]?token|"
    r"access[_-]?token|refresh[_-]?token|session[_-]?token|license[_-]?key|private[_-]?key|"
    r"secret[_-]?key|webhook[_-]?url|authorization|proxy[_-]?authorization|cookie|set[_-]?cookie)"
    r"([=:]\s*)[^\s,;&]+",
    re.IGNORECASE,
)
_TOKEN_LIKE_PATTERN = re.compile(
    r"\b(?:sk-[a-z0-9_\-]{16,}|xox[baprs]-[a-z0-9\-]{16,}|gh[pousr]_[a-z0-9_]{20,})\b",
    re.IGNORECASE,
)


def sanitize_diagnostic_text(text: Any, *, max_length: int = 300) -> str:
    """脱敏诊断文本中的常见密钥与 URL，并按字节上限截断。

    Args:
        text: 任意可被 str() 的对象。
        max_length: 返回字符串的最大长度。

    Returns:
        脱敏并截断后的字符串；空输入返回空串。
    """
    """Redact common secrets and URLs from diagnostic text."""
    sanitized = str(text or "").strip()
    if not sanitized:
        return ""
    sanitized = _AUTHORIZATION_HEADER_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _COOKIE_HEADER_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _BEARER_PATTERN.sub(r"\1[REDACTED]", sanitized)
    sanitized = re.sub(r"(?i)(token|secret|password|sendkey)([=:]\s*)[^\s,;&]+", r"\1\2[REDACTED]", sanitized)
    sanitized = re.sub(r"https?://[^\s]+", "[REDACTED_URL]", sanitized)
    return " ".join(sanitized.split())[:max_length]


def redact_sensitive_mapping(obj: Any) -> Any:
    """按键名递归脱敏映射/列表中的敏感字段。

    Args:
        obj: 任意 dict/list/标量。

    Returns:
        与 obj 结构对应的脱敏副本；标量原样返回。

    Notes:
        该函数仅基于键名做启发式判断，不会对任意字符串值做正则扫描。
        早期只为 `AnalysisContextPack` 字典提供一个确定的序列化路径（P1 范围）。
    """
    """Recursively redact sensitive values from mappings by key name only.

    This helper intentionally does not inspect arbitrary string values. P1 only
    needs a deterministic serializer for AnalysisContextPack dictionaries.
    """
    if isinstance(obj, dict):
        redacted = {}
        for key, value in obj.items():
            if _is_sensitive_mapping_key(key):
                redacted[key] = _REDACTED
            else:
                redacted[key] = redact_sensitive_mapping(value)
        return redacted
    if isinstance(obj, list):
        return [redact_sensitive_mapping(item) for item in obj]
    return obj


def sanitize_decision_signal_text(text: Any) -> str:
    """脱敏决策信号文本中明显的密钥，但不做长度截断。"""
    """Redact obvious secrets from persisted decision-signal text without truncating."""
    sanitized = str(text or "").strip()
    if not sanitized:
        return ""
    sanitized = _URL_PATTERN.sub(_redact_sensitive_url_match, sanitized)
    sanitized = _AUTHORIZATION_HEADER_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _COOKIE_HEADER_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _BEARER_PATTERN.sub(r"\1[REDACTED]", sanitized)
    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _TOKEN_LIKE_PATTERN.sub("[REDACTED]", sanitized)
    return " ".join(sanitized.split())


def sanitize_decision_signal_payload(obj: Any) -> Any:
    """对决策信号 JSON payload 同时按敏感键与字符串内容做清洗。"""
    """Redact decision-signal JSON payloads by sensitive keys and string values."""
    redacted = redact_sensitive_mapping(obj)
    return _sanitize_decision_signal_payload_values(redacted)


def _sanitize_decision_signal_payload_values(obj: Any) -> Any:
    """递归遍历 obj，把所有字符串走 sanitize_decision_signal_text。"""
    if isinstance(obj, dict):
        return {
            key: _sanitize_decision_signal_payload_values(value)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_sanitize_decision_signal_payload_values(item) for item in obj]
    if isinstance(obj, str):
        return sanitize_decision_signal_text(obj)
    return obj


def _redact_sensitive_url_match(match: re.Match[str]) -> str:
    """对 URL 正则匹配项做敏感判定后返回脱敏或原文。"""
    url = match.group(0)
    if _is_sensitive_url(url):
        return "[REDACTED_URL]"
    return url


def _is_sensitive_url(url: str) -> bool:
    """判断 URL 是否含令牌、账号口令、Webhook 或敏感参数等敏感信息。"""
    if _TOKEN_LIKE_PATTERN.search(url):
        return True
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.username or parsed.password:
        return True
    if _is_webhook_url(parsed.hostname or "", parsed.path):
        return True
    return (
        _has_sensitive_url_params(parsed.query)
        or _has_sensitive_url_params(parsed.fragment)
    )


def _is_webhook_url(hostname: str, path: str) -> bool:
    """判断给定 hostname + path 是否为已知的 Webhook 端点形态。"""
    hostname = str(hostname or "").lower().strip(".")
    normalized_path = f"/{path.lstrip('/').lower()}"
    path_segments = [segment for segment in normalized_path.split("/") if segment]

    if hostname == "hooks.slack.com" and normalized_path.startswith("/services/"):
        return True
    if hostname in {"discord.com", "discordapp.com"} and "/api/webhooks/" in normalized_path:
        return True
    if hostname == "open.feishu.cn" and "/open-apis/bot/" in normalized_path and "/hook/" in normalized_path:
        return True
    if hostname == "oapi.dingtalk.com" and normalized_path.startswith("/robot/send"):
        return True
    if hostname == "qyapi.weixin.qq.com" and normalized_path.startswith("/cgi-bin/webhook/send"):
        return True
    if hostname in {"sctapi.ftqq.com", "sc.ftqq.com"}:
        return True
    if hostname.startswith("hooks."):
        return True
    if {"hook", "webhook", "webhooks"} & set(path_segments):
        return True
    return False


def _has_sensitive_url_params(params_text: str) -> bool:
    """判断 URL query/fragment 是否包含敏感键或形似 token 的值。"""
    if not params_text:
        return False
    try:
        params = parse_qsl(params_text, keep_blank_values=True)
    except ValueError:
        return False
    for key, value in params:
        key_text = str(key or "").strip().lower()
        if _is_sensitive_mapping_key(key_text):
            return True
        if _TOKEN_LIKE_PATTERN.search(str(value or "")):
            return True
    return False


def _is_sensitive_mapping_key(key: Any) -> bool:
    """判断键名是否落在敏感字段词表内（含下划线/连字符/大小写归一）。"""
    key_text = str(key or "").strip()
    if not key_text:
        return False
    parts = _mapping_key_parts(key_text)
    if _has_sensitive_phrase("_".join(parts)):
        return True
    return bool(set(parts) & _SENSITIVE_KEY_PARTS)


def _has_sensitive_phrase(normalized_key: str) -> bool:
    """判断归一化后的键名是否包含任一敏感词组（支持下划线/紧凑形态/正则匹配）。"""
    padded_key = f"_{normalized_key}_"
    if any(f"_{phrase}_" in padded_key for phrase in _SENSITIVE_KEY_PHRASES):
        return True
    compact_key = normalized_key.replace("_", "")
    if any(phrase in compact_key for phrase in _SENSITIVE_COMPACT_KEY_PHRASES):
        return True
    return bool(_SENSITIVE_COMPACT_KEY_PATTERN.search(compact_key))


def _mapping_key_parts(key_text: str) -> list[str]:
    """把 camelCase、kebab-case、snake_case 形式的键名拆分为小写词片段。"""
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key_text)
    return [
        part.lower()
        for part in re.split(r"[^A-Za-z0-9]+", split_camel)
        if part
    ]
