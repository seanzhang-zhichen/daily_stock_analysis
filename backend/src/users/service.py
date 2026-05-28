# -*- coding: utf-8 -*-
"""用户体系 service 层: 编排注册 / 登录 / 邮箱验证 / 密码重置等用例。

调用方 (api/v1/endpoints/account.py) 只需关心:

1. 注入一个 SQLAlchemy ``Session`` (来自 ``api.deps.get_db``)。
2. 捕获 :class:`src.users.errors.UserError` 并翻译成 HTTP 响应。

service 不直接处理 cookie / HTTP 头, 这些放在 endpoint 层。
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.storage import AppUser
from src.users import repository as repo
from src.users.config import UserModeSettings, load_user_mode_settings
from src.users.consents import CURRENT_TERMS_VERSION, record_consent
from src.users.email import EmailBackend, EmailMessageDTO, get_email_backend
from src.users.unsubscribe import get_frontend_public_base_url
from src.users.errors import UserError, UserErrorCode
from src.users.passwords import (
    hash_password,
    is_valid_email,
    validate_password_strength,
    verify_password,
)
from src.users.registration_guard import (
    RegistrationGuardConfig,
    preflight_registration,
    record_registration_attempt,
)
from src.users.sessions import (
    IssuedSession,
    issue_session,
    revoke_all_user_sessions,
)


logger = logging.getLogger(__name__)


# --- Rate limit (登录失败 / 重置请求) ---------------------------------------

_RATE_LIMIT_WINDOW_SEC = 300
_RATE_LIMIT_MAX_FAILURES = 8
_rate_lock = threading.Lock()
_rate_state: dict[str, Tuple[int, float]] = {}


def _rate_check(key: str) -> bool:
    now = time.time()
    with _rate_lock:
        for k, (_, ts) in list(_rate_state.items()):
            if now - ts > _RATE_LIMIT_WINDOW_SEC:
                _rate_state.pop(k, None)
        count, _ = _rate_state.get(key, (0, now))
        return count < _RATE_LIMIT_MAX_FAILURES


def _rate_record_failure(key: str) -> None:
    now = time.time()
    with _rate_lock:
        count, first = _rate_state.get(key, (0, now))
        if now - first > _RATE_LIMIT_WINDOW_SEC:
            _rate_state[key] = (1, now)
        else:
            _rate_state[key] = (count + 1, first)


def _rate_clear(key: str) -> None:
    with _rate_lock:
        _rate_state.pop(key, None)


# --- 共享前置校验 -----------------------------------------------------------


def _ensure_mode_enabled(settings: UserModeSettings) -> None:
    return None


def _normalize_email(email: str) -> str:
    if not is_valid_email(email or ""):
        raise UserError(UserErrorCode.INVALID_EMAIL, "请输入合法邮箱")
    return email.strip().lower()


def _validate_or_raise(password: str) -> None:
    err = validate_password_strength(password)
    if err:
        raise UserError(UserErrorCode.INVALID_PASSWORD, err)


# --- 用例 ------------------------------------------------------------------


@dataclass(frozen=True)
class RegistrationResult:
    user: AppUser


def register_user(
    db: Session,
    *,
    email: str,
    password: str,
    password_confirm: str,
    invite_code: Optional[str] = None,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
    email_backend: Optional[EmailBackend] = None,
    settings: Optional[UserModeSettings] = None,
    terms_version: Optional[str] = None,
    terms_agreed: bool = False,
) -> RegistrationResult:
    """完整的邮箱密码注册用例。

    Phase 6: 注册必须显式同意协议三件套, 否则拒绝注册;
    同时落一条 :class:`AppUserConsent` 记录用于合规审计。
    """
    settings = settings or load_user_mode_settings()
    _ensure_mode_enabled(settings)
    if not settings.public_registration_enabled:
        raise UserError(UserErrorCode.REGISTRATION_DISABLED, "当前未开放注册, 请联系管理员")

    if settings.invite_codes:
        code = (invite_code or "").strip()
        if not code:
            raise UserError(UserErrorCode.INVITE_CODE_REQUIRED, "请输入邀请码")
        if code not in settings.invite_codes:
            raise UserError(UserErrorCode.INVITE_CODE_INVALID, "邀请码无效")

    if password != password_confirm:
        raise UserError(UserErrorCode.PASSWORD_MISMATCH, "两次输入的密码不一致")
    _validate_or_raise(password)

    email_normalized = _normalize_email(email)

    # Phase 6 §5.8.1 注册防刷: 一次性邮箱黑名单 + IP/邮箱限频。
    # 在查询 existing user 之前先执行, 一方面避免「邮箱已存在」与「注册被风控」两条
    # 错误信息被穷举区分, 另一方面让每次尝试 (含命中 disposable 黑名单) 都被纳入风控统计。
    guard_cfg = RegistrationGuardConfig(
        disposable_block_enabled=settings.register_disposable_block,
        ip_daily_max=settings.register_ip_daily_max,
        email_daily_max=settings.register_email_daily_max,
        window_hours=settings.register_rate_window_hours,
        mx_check_enabled=settings.register_mx_check_enabled,
        disposable_domains=settings.disposable_email_domains,
        disposable_domains_replace=settings.disposable_email_domains_replace,
    )
    record_registration_attempt(db, email=email_normalized, ip=ip, user_agent=user_agent)
    preflight_registration(
        db,
        email=email_normalized,
        ip=ip,
        user_agent=user_agent,
        config=guard_cfg,
    )

    existing = repo.get_user_by_email(db, email_normalized)
    if existing is not None:
        raise UserError(UserErrorCode.EMAIL_ALREADY_REGISTERED, "该邮箱已注册")

    # Phase 6: 通过基础校验后才检查协议同意, 这样字段错误能更精确反馈
    if not terms_agreed:
        raise UserError(
            UserErrorCode.VALIDATION_ERROR,
            "请阅读并同意《用户服务协议》《隐私政策》《投资风险揭示书》后再注册",
        )

    expected_version = (settings.terms_version or CURRENT_TERMS_VERSION).strip()
    submitted_version = (terms_version or "").strip()
    if submitted_version and submitted_version != expected_version:
        raise UserError(
            UserErrorCode.VALIDATION_ERROR,
            "协议版本已更新, 请刷新页面后重新确认协议",
        )
    accepted_version = submitted_version or expected_version

    try:
        user = repo.create_user(
            db,
            email=email_normalized,
            password_hash=hash_password(password),
            plan_code="free",
            email_verified=False,
        )
    except IntegrityError as exc:
        db.rollback()
        logger.info("Concurrent registration for %s: %s", email_normalized, exc)
        raise UserError(UserErrorCode.EMAIL_ALREADY_REGISTERED, "该邮箱已注册") from exc

    # 记录注册时同意协议的版本号 + IP + UA (Phase 6 合规需要)
    record_consent(
        db,
        user=user,
        terms_version=accepted_version,
        purpose="register",
        ip=ip,
        user_agent=user_agent,
    )

    token = secrets.token_urlsafe(32)
    repo.create_verification_token(
        db,
        user_id=user.id,
        raw_token=token,
        purpose="verify",
        ttl_hours=settings.verification_ttl_hours,
    )
    verify_url = f"{get_frontend_public_base_url()}/verify-email?token={token}"
    backend = email_backend or get_email_backend()
    backend.send(
        EmailMessageDTO(
            to=user.email,
            subject="验证你的邮箱 - DSA 智能分析",
            body_text=(
                "你好,\n\n"
                "请点击以下链接完成邮箱验证，激活你的 DSA 智能分析账号：\n\n"
                f"{verify_url}\n\n"
                f"链接 {settings.verification_ttl_hours} 小时内有效，点击一次即可完成验证。\n\n"
                "若无法点击链接，请复制上方地址到浏览器中打开。\n\n"
                "若不是你本人操作，请忽略本邮件。"
            ),
        )
    )

    return RegistrationResult(user=user)


def login(
    db: Session,
    *,
    email: str,
    password: str,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
    settings: Optional[UserModeSettings] = None,
) -> IssuedSession:
    settings = settings or load_user_mode_settings()
    _ensure_mode_enabled(settings)

    email_normalized = _normalize_email(email)
    rl_key = f"login:{email_normalized}:{ip or '-'}"
    if not _rate_check(rl_key):
        raise UserError(UserErrorCode.RATE_LIMITED, "登录尝试过于频繁, 请稍后再试")

    user = repo.get_user_by_email(db, email_normalized)
    if user is None or not verify_password(password or "", user.password_hash):
        _rate_record_failure(rl_key)
        raise UserError(UserErrorCode.INVALID_CREDENTIALS, "邮箱或密码错误")

    if user.status != "active":
        raise UserError(UserErrorCode.USER_DISABLED, "账户已禁用, 请联系管理员")

    if settings.require_email_verification and user.email_verified_at is None:
        raise UserError(UserErrorCode.EMAIL_NOT_VERIFIED, "请先完成邮箱验证")

    _rate_clear(rl_key)
    repo.touch_last_login(db, user)
    return issue_session(
        db,
        user,
        ttl_hours=settings.session_ttl_hours,
        user_agent=user_agent,
        ip=ip,
    )


def verify_email(
    db: Session,
    *,
    token: str,
    settings: Optional[UserModeSettings] = None,
) -> AppUser:
    settings = settings or load_user_mode_settings()
    _ensure_mode_enabled(settings)
    row = repo.consume_verification_token(db, raw_token=token, purpose="verify")
    if row is None:
        raise UserError(UserErrorCode.INVALID_TOKEN, "验证码无效或已过期")
    user = repo.get_user_by_id(db, row.user_id)
    if user is None:
        raise UserError(UserErrorCode.INVALID_TOKEN, "验证码无效或已过期")
    return repo.mark_email_verified(db, user)


def request_password_reset(
    db: Session,
    *,
    email: str,
    email_backend: Optional[EmailBackend] = None,
    settings: Optional[UserModeSettings] = None,
) -> None:
    """发送密码重置邮件。即便邮箱不存在也不会暴露, 调用方按 200 处理。"""
    settings = settings or load_user_mode_settings()
    _ensure_mode_enabled(settings)
    email_normalized = _normalize_email(email)

    user = repo.get_user_by_email(db, email_normalized)
    if user is None:
        logger.info("password reset requested for unknown email %s", email_normalized)
        return

    token = secrets.token_urlsafe(32)
    repo.create_verification_token(
        db,
        user_id=user.id,
        raw_token=token,
        purpose="reset",
        ttl_hours=settings.reset_ttl_hours,
    )
    backend = email_backend or get_email_backend()
    backend.send(
        EmailMessageDTO(
            to=user.email,
            subject="重置密码 - DSA 智能分析",
            body_text=(
                "你好,\n\n请使用以下重置 token 完成密码重置:\n\n"
                f"{token}\n\n"
                f"该 token {settings.reset_ttl_hours} 小时内有效, 仅可使用一次。\n\n"
                "若不是你本人请求, 请忽略本邮件并考虑修改密码。"
            ),
        )
    )


def reset_password(
    db: Session,
    *,
    token: str,
    new_password: str,
    new_password_confirm: str,
    settings: Optional[UserModeSettings] = None,
) -> AppUser:
    settings = settings or load_user_mode_settings()
    _ensure_mode_enabled(settings)
    if new_password != new_password_confirm:
        raise UserError(UserErrorCode.PASSWORD_MISMATCH, "两次输入的密码不一致")
    _validate_or_raise(new_password)

    row = repo.consume_verification_token(db, raw_token=token, purpose="reset")
    if row is None:
        raise UserError(UserErrorCode.INVALID_TOKEN, "重置链接无效或已过期")

    user = repo.get_user_by_id(db, row.user_id)
    if user is None:
        raise UserError(UserErrorCode.INVALID_TOKEN, "重置链接无效或已过期")

    repo.update_password(db, user, hash_password(new_password))
    revoke_all_user_sessions(db, user.id)
    return user


def change_password(
    db: Session,
    *,
    user: AppUser,
    current_password: str,
    new_password: str,
    new_password_confirm: str,
    settings: Optional[UserModeSettings] = None,
) -> AppUser:
    settings = settings or load_user_mode_settings()
    _ensure_mode_enabled(settings)
    if not verify_password(current_password or "", user.password_hash):
        raise UserError(UserErrorCode.INVALID_CREDENTIALS, "当前密码错误")
    if new_password != new_password_confirm:
        raise UserError(UserErrorCode.PASSWORD_MISMATCH, "两次输入的新密码不一致")
    _validate_or_raise(new_password)
    repo.update_password(db, user, hash_password(new_password))
    revoke_all_user_sessions(db, user.id)
    return user
