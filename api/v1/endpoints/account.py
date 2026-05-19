# -*- coding: utf-8 -*-
"""C 端用户账号 endpoint (Phase 1)。

挂载位置: ``/api/v1/account/*``。

提供多用户账户注册、登录、状态查询、密码与 BYOK 管理等接口。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_db
from src.auth import get_client_ip
from src.users.config import (
    SESSION_COOKIE_NAME,
    UserModeSettings,
    load_user_mode_settings,
)
from src.users.consents import CURRENT_TERMS_VERSION, needs_reaccept
from src.users.byok import (
    SUPPORTED_PROVIDERS,
    delete_credential as svc_delete_byok,
    list_credentials as svc_list_byok,
    upsert_credential as svc_upsert_byok,
)
from src.users.errors import UserError, UserErrorCode
from src.users.notification_prefs import (
    update_prefs as svc_update_prefs,
    get_prefs as svc_get_prefs,
)
from src.users.unsubscribe import (
    ACTION_DAILY,
    ACTION_EMAIL,
    verify_unsubscribe_token,
)
from src.users.plans import (
    redeem_code as svc_redeem_code,
    resolve_user_plan as svc_resolve_user_plan,
)
from src.users.plan_lifecycle import REMINDER_OFFSET_DAYS
from src.users.watchlist import (
    add_stock as svc_add_stock,
    list_stocks as svc_list_stocks,
    remove_stock as svc_remove_stock,
    set_watchlist as svc_set_watchlist,
)
from src.users.quota import get_quota_snapshot
from src.users.service import (
    change_password as svc_change_password,
    login as svc_login,
    register_user as svc_register,
    request_password_reset as svc_request_password_reset,
    reset_password as svc_reset_password,
    verify_email as svc_verify_email,
)
from src.users.sessions import IssuedSession, resolve_session, revoke_session
from src.users.audit import write_audit_log
from src.users.deletion import (
    cancel_deletion as svc_cancel_deletion,
    request_deletion as svc_request_deletion,
    COOLING_OFF_DAYS as _DELETION_COOLING_OFF_DAYS,
)
from src.users.data_export import request_data_export as svc_request_data_export

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Schemas ---------------------------------------------------------------


class RegisterRequest(BaseModel):
    model_config = {"populate_by_name": True}

    email: str = Field(default="")
    password: str = Field(default="")
    password_confirm: str = Field(default="", alias="passwordConfirm")
    invite_code: str | None = Field(default=None, alias="inviteCode")
    # Phase 6: 注册必须显式同意协议三件套
    terms_agreed: bool = Field(default=False, alias="termsAgreed")
    terms_version: str | None = Field(default=None, alias="termsVersion")


class LoginRequest(BaseModel):
    email: str = Field(default="")
    password: str = Field(default="")


class VerifyEmailRequest(BaseModel):
    token: str = Field(default="")


class RequestResetRequest(BaseModel):
    email: str = Field(default="")


class ResetPasswordRequest(BaseModel):
    model_config = {"populate_by_name": True}

    token: str = Field(default="")
    new_password: str = Field(default="", alias="newPassword")
    new_password_confirm: str = Field(default="", alias="newPasswordConfirm")


class ChangePasswordRequest(BaseModel):
    model_config = {"populate_by_name": True}

    current_password: str = Field(default="", alias="currentPassword")
    new_password: str = Field(default="", alias="newPassword")
    new_password_confirm: str = Field(default="", alias="newPasswordConfirm")


class RedeemRequest(BaseModel):
    code: str = Field(default="")


class ByokUpsertRequest(BaseModel):
    model_config = {"populate_by_name": True}

    provider: str = Field(default="")
    api_key: str = Field(default="", alias="apiKey")
    base_url: str | None = Field(default=None, alias="baseUrl")
    model: str | None = Field(default=None)


class WatchlistAddRequest(BaseModel):
    model_config = {"populate_by_name": True}

    stock_code: str = Field(default="", alias="stockCode")
    stock_name: str | None = Field(default=None, alias="stockName")


class WatchlistSetRequest(BaseModel):
    model_config = {"populate_by_name": True}

    stocks: list[dict] = Field(default_factory=list)


class NotificationPrefsUpdateRequest(BaseModel):
    model_config = {"populate_by_name": True}

    daily_push_enabled: bool | None = Field(default=None, alias="dailyPushEnabled")
    email_enabled: bool | None = Field(default=None, alias="emailEnabled")
    webhook_url: str | None = Field(default=None, alias="webhookUrl")
    webhook_type: str | None = Field(default=None, alias="webhookType")
    clear_webhook: bool = Field(default=False, alias="clearWebhook")


# --- Helpers ---------------------------------------------------------------


_USER_ERROR_HTTP_STATUS = {
    UserErrorCode.REGISTRATION_DISABLED: 403,
    UserErrorCode.INVALID_EMAIL: 400,
    UserErrorCode.INVALID_PASSWORD: 400,
    UserErrorCode.PASSWORD_MISMATCH: 400,
    UserErrorCode.EMAIL_ALREADY_REGISTERED: 409,
    UserErrorCode.INVALID_CREDENTIALS: 401,
    UserErrorCode.EMAIL_NOT_VERIFIED: 403,
    UserErrorCode.USER_DISABLED: 403,
    UserErrorCode.INVALID_TOKEN: 400,
    UserErrorCode.TOKEN_EXPIRED: 400,
    UserErrorCode.INVITE_CODE_REQUIRED: 400,
    UserErrorCode.INVITE_CODE_INVALID: 400,
    UserErrorCode.RATE_LIMITED: 429,
    UserErrorCode.QUOTA_EXCEEDED: 422,
    UserErrorCode.VALIDATION_ERROR: 400,
    UserErrorCode.NOT_FOUND: 404,
    UserErrorCode.PERMISSION_DENIED: 403,
}


def _user_error_response(exc: UserError) -> JSONResponse:
    status = _USER_ERROR_HTTP_STATUS.get(exc.code, 400)
    return JSONResponse(
        status_code=status,
        content={"error": exc.code.value, "message": exc.message},
    )


def _cookie_kwargs(request: Request, settings: UserModeSettings) -> dict:
    secure = False
    if os.getenv("TRUST_X_FORWARDED_FOR", "false").lower() == "true":
        proto = request.headers.get("X-Forwarded-Proto", "").lower()
        secure = proto == "https"
    else:
        secure = request.url.scheme == "https"
    return {
        "key": SESSION_COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": "/",
        "max_age": settings.session_ttl_hours * 3600,
    }


def _attach_session_cookie(
    response: Response,
    request: Request,
    issued: IssuedSession,
    settings: UserModeSettings,
) -> None:
    params = _cookie_kwargs(request, settings)
    response.set_cookie(value=issued.cookie_value, **params)


def _serialize_user(user) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "plan": user.plan_code,
        "planExpiresAt": user.plan_expires_at.isoformat() if user.plan_expires_at else None,
        "emailVerified": user.email_verified_at is not None,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "lastLoginAt": user.last_login_at.isoformat() if user.last_login_at else None,
        "isAdmin": bool(getattr(user, "is_admin", False)),
        "termsVersion": getattr(user, "terms_version", None),
        "needsReacceptTerms": needs_reaccept(user),
    }


def _build_renewal_payload(user) -> Optional[dict]:
    """渲染前端续费提示所需的 ``renewal`` 字段。

    - free 档 / 无到期时间: 返回 ``None``
    - 已过期 (理论上 `plan_lifecycle` 会自动降级, 这里兜底): ``expired=True``
    - 距到期 ≤ max(REMINDER_OFFSET_DAYS) 天: ``willExpireSoon=True``
    - 其它情况返回基础剩余天数信息, 供前端按需展示
    """
    plan_code = (getattr(user, "plan_code", None) or "free").strip().lower()
    expires_at = getattr(user, "plan_expires_at", None)
    if plan_code == "free" or expires_at is None:
        return None

    now = datetime.utcnow()
    delta_seconds = (expires_at - now).total_seconds()
    expired = delta_seconds < 0
    # 与 plan_lifecycle.find_users_needing_reminder 保持一致, 向上取整避免临界点丢失
    days_remaining = int(delta_seconds // 86400)
    if delta_seconds > 0 and delta_seconds % 86400 > 0:
        days_remaining += 1
    if expired:
        days_remaining = 0
    threshold = max(REMINDER_OFFSET_DAYS) if REMINDER_OFFSET_DAYS else 7
    will_expire_soon = (not expired) and days_remaining <= threshold

    return {
        "planCode": plan_code,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "daysRemaining": days_remaining,
        "willExpireSoon": will_expire_soon,
        "expired": expired,
        "thresholdDays": threshold,
    }


def _status_payload(
    settings: UserModeSettings,
    request: Request,
    db: Session,
) -> dict:
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    user = resolve_session(db, cookie_value) if cookie_value else None

    quota_payload = None
    plan_payload = None
    renewal_payload = None
    if user is not None:
        plan = svc_resolve_user_plan(db, user, settings=settings)
        plan_payload = {
            "code": plan.code,
            "name": plan.name,
            "isPro": plan.is_pro,
            "dailyAnalysisLimit": plan.daily_analysis_limit,
            "dailyAgentLimit": plan.daily_agent_limit,
            "maxStocks": plan.max_stocks,
            "canByok": plan.can_byok,
            "canWebhook": plan.can_webhook,
            "expiresAt": plan.expires_at.isoformat() if plan.expires_at else None,
        }
        snapshot = get_quota_snapshot(
            db,
            user_id=user.id,
            analysis_limit=plan.daily_analysis_limit,
            agent_limit=plan.daily_agent_limit,
        )
        quota_payload = {
            "analysisUsed": snapshot.analysis_used,
            "analysisLimit": snapshot.analysis_limit,
            "analysisRemaining": snapshot.analysis_remaining,
            "agentUsed": snapshot.agent_used,
            "agentLimit": snapshot.agent_limit,
            "agentRemaining": snapshot.agent_remaining,
        }
        renewal_payload = _build_renewal_payload(user)

    return {
        "userModeEnabled": True,
        "registrationEnabled": settings.public_registration_enabled,
        "requireEmailVerification": settings.require_email_verification,
        "inviteRequired": bool(settings.invite_codes),
        "loggedIn": user is not None,
        "user": _serialize_user(user) if user is not None else None,
        "limits": {
            "freeDailyAnalysis": settings.free_daily_analysis,
            "freeDailyAgent": settings.free_daily_agent,
            "freeMaxStocks": settings.free_max_stocks,
        },
        "plan": plan_payload,
        "quota": quota_payload,
        "renewal": renewal_payload,
        "termsVersion": CURRENT_TERMS_VERSION,
    }


def _get_settings_or_disabled():
    return load_user_mode_settings()


def _commit_or_rollback(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


# --- Endpoints -------------------------------------------------------------


@router.get("/status", summary="C 端用户态")
async def account_status(request: Request, db: Session = Depends(get_db)):
    """无需登录即可访问, 用于前端启动时拉取用户态。"""
    settings = load_user_mode_settings()
    return _status_payload(settings, request, db)


@router.post("/register", summary="注册新用户")
async def account_register(
    request: Request,
    body: RegisterRequest,
    db: Session = Depends(get_db),
):
    try:
        settings = _get_settings_or_disabled()
        result = svc_register(
            db,
            email=body.email,
            password=body.password,
            password_confirm=body.password_confirm,
            invite_code=body.invite_code,
            user_agent=request.headers.get("user-agent"),
            ip=get_client_ip(request),
            settings=settings,
            terms_agreed=bool(body.terms_agreed),
            terms_version=body.terms_version,
        )
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("register failed")
        return JSONResponse(status_code=500, content={"error": "internal_error", "message": "注册失败, 请稍后重试"})

    write_audit_log(
        db, "auth.register",
        user_id=int(result.user.id),
        ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return JSONResponse(content={"user": _serialize_user(result.user)})


@router.post("/login", summary="邮箱密码登录")
async def account_login(
    request: Request,
    body: LoginRequest,
    db: Session = Depends(get_db),
):
    try:
        settings = _get_settings_or_disabled()
        issued = svc_login(
            db,
            email=body.email,
            password=body.password,
            user_agent=request.headers.get("user-agent"),
            ip=get_client_ip(request),
            settings=settings,
        )
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("login failed")
        return JSONResponse(status_code=500, content={"error": "internal_error", "message": "登录失败, 请稍后重试"})

    write_audit_log(
        db, "auth.login",
        user_id=int(issued.user.id),
        ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    response = JSONResponse(content={"user": _serialize_user(issued.user)})
    _attach_session_cookie(response, request, issued, settings)
    return response


@router.post("/logout", summary="登出当前 session")
async def account_logout(request: Request, db: Session = Depends(get_db)):
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    revoked = False
    if cookie_value:
        try:
            revoked = revoke_session(db, cookie_value)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("logout failed to revoke session")

    response = Response(status_code=204)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    if not revoked:
        # Even if no matching record, we still clear cookie - that's fine.
        pass
    return response


@router.post("/verify-email", summary="邮箱验证")
async def account_verify_email(body: VerifyEmailRequest, db: Session = Depends(get_db)):
    try:
        settings = _get_settings_or_disabled()
        user = svc_verify_email(db, token=body.token, settings=settings)
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    return {"user": _serialize_user(user)}


@router.post("/request-password-reset", summary="发起密码重置邮件")
async def account_request_reset(body: RequestResetRequest, db: Session = Depends(get_db)):
    try:
        settings = _get_settings_or_disabled()
        try:
            svc_request_password_reset(db, email=body.email, settings=settings)
        except UserError as exc:
            # 邮箱格式错误等仍按错误返回, 真正"邮箱不存在"已被 service 静默化
            db.rollback()
            return _user_error_response(exc)
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("request password reset failed")
        return JSONResponse(status_code=500, content={"error": "internal_error", "message": "请稍后重试"})

    return {"ok": True}


@router.post("/reset-password", summary="使用 token 重置密码")
async def account_reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        settings = _get_settings_or_disabled()
        svc_reset_password(
            db,
            token=body.token,
            new_password=body.new_password,
            new_password_confirm=body.new_password_confirm,
            settings=settings,
        )
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    write_audit_log(db, "auth.reset_password", detail={"token_prefix": body.token[:8]})
    return Response(status_code=204)


def _require_current_user(request: Request, db: Session):
    settings = load_user_mode_settings()
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    user = resolve_session(db, cookie_value) if cookie_value else None
    if user is None:
        raise UserError(UserErrorCode.INVALID_CREDENTIALS, "请先登录")
    return settings, user


@router.get("/me", summary="当前登录用户信息")
async def account_me(request: Request, db: Session = Depends(get_db)):
    try:
        _, user = _require_current_user(request, db)
    except UserError as exc:
        return _user_error_response(exc)
    return {"user": _serialize_user(user)}


@router.post("/change-password", summary="登录态下修改密码")
async def account_change_password(
    request: Request,
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
):
    try:
        settings, user = _require_current_user(request, db)
        svc_change_password(
            db,
            user=user,
            current_password=body.current_password,
            new_password=body.new_password,
            new_password_confirm=body.new_password_confirm,
            settings=settings,
        )
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    write_audit_log(
        db, "auth.change_password",
        user_id=int(user.id),
        ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    response = Response(status_code=204)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response


# ============================================================
# Redeem code (Phase 2)
# ============================================================


@router.post("/redeem", summary="使用兑换码升级套餐")
async def account_redeem(
    request: Request,
    body: RedeemRequest,
    db: Session = Depends(get_db),
):
    try:
        settings, user = _require_current_user(request, db)
        sub = svc_redeem_code(db, user, code=body.code)
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("redeem failed")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "兑换失败, 请稍后重试"},
        )

    write_audit_log(
        db, "plan.redeem",
        user_id=int(user.id),
        target_ref=body.code,
        detail={"planCode": sub.plan_code},
        ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.refresh(user)
    plan = svc_resolve_user_plan(db, user, settings=settings)
    return {
        "user": _serialize_user(user),
        "subscription": {
            "id": sub.id,
            "planCode": sub.plan_code,
            "source": sub.source,
            "startedAt": sub.started_at.isoformat() if sub.started_at else None,
            "expiresAt": sub.expires_at.isoformat() if sub.expires_at else None,
            "note": sub.note,
        },
        "plan": {
            "code": plan.code,
            "name": plan.name,
            "isPro": plan.is_pro,
            "dailyAnalysisLimit": plan.daily_analysis_limit,
            "dailyAgentLimit": plan.daily_agent_limit,
            "maxStocks": plan.max_stocks,
            "canByok": plan.can_byok,
            "canWebhook": plan.can_webhook,
            "expiresAt": plan.expires_at.isoformat() if plan.expires_at else None,
        },
    }


# ============================================================
# BYOK API keys (Phase 4 backend ready, exposing the management endpoints)
# ============================================================


def _serialize_byok(view) -> dict:
    return {
        "id": view.id,
        "provider": view.provider,
        "baseUrl": view.base_url,
        "model": view.model,
        "status": view.status,
        "keyPreview": view.key_preview,
        "updatedAt": view.updated_at.isoformat() if view.updated_at else None,
    }


@router.get("/api-keys", summary="列出当前用户的 BYOK Key (脱敏)")
async def account_list_api_keys(request: Request, db: Session = Depends(get_db)):
    try:
        settings, user = _require_current_user(request, db)
    except UserError as exc:
        return _user_error_response(exc)

    plan = svc_resolve_user_plan(db, user, settings=settings)
    views = svc_list_byok(db, user_id=user.id)
    db.commit()
    return {
        "supportedProviders": sorted(SUPPORTED_PROVIDERS),
        "canByok": plan.can_byok,
        "credentials": [_serialize_byok(v) for v in views],
    }


@router.post("/api-keys", summary="新增或更新一个 provider 的 BYOK Key")
async def account_upsert_api_key(
    request: Request,
    body: ByokUpsertRequest,
    db: Session = Depends(get_db),
):
    try:
        settings, user = _require_current_user(request, db)
        plan = svc_resolve_user_plan(db, user, settings=settings)
        if not plan.can_byok:
            raise UserError(UserErrorCode.REGISTRATION_DISABLED, "当前套餐不支持 BYOK, 请先升级到 Pro")
        view = svc_upsert_byok(
            db,
            user=user,
            provider=body.provider,
            api_key=body.api_key,
            base_url=body.base_url,
            model=body.model,
        )
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("byok upsert failed")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "保存 API Key 失败, 请稍后重试"},
        )
    write_audit_log(
        db, "byok.upsert",
        user_id=int(user.id),
        target_ref=body.provider,
        ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"credential": _serialize_byok(view)}


@router.delete("/api-keys/{provider}", summary="删除指定 provider 的 BYOK Key")
async def account_delete_api_key(
    provider: str,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        _, user = _require_current_user(request, db)
        deleted = svc_delete_byok(db, user_id=user.id, provider=provider)
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("byok delete failed")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "删除失败, 请稍后重试"},
        )
    if not deleted:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": "未找到对应的 API Key"},
        )
    write_audit_log(
        db, "byok.delete",
        user_id=int(user.id),
        target_ref=provider,
        ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return Response(status_code=204)


# ============================================================
# Watchlist (Phase 3) - per-user 自选股管理
# ============================================================


def _serialize_watchlist_item(item) -> dict:
    return {"stockCode": item.stock_code, "stockName": item.stock_name}


@router.get("/watchlist", summary="获取当前用户自选股列表")
async def account_get_watchlist(request: Request, db: Session = Depends(get_db)):
    try:
        settings, user = _require_current_user(request, db)
        plan = svc_resolve_user_plan(db, user, settings=settings)
    except UserError as exc:
        return _user_error_response(exc)

    items = svc_list_stocks(db, user_id=user.id)
    db.commit()
    return {
        "stocks": [_serialize_watchlist_item(i) for i in items],
        "count": len(items),
        "maxStocks": plan.max_stocks,
    }


@router.post("/watchlist", summary="添加一只自选股")
async def account_add_watchlist(
    request: Request,
    body: WatchlistAddRequest,
    db: Session = Depends(get_db),
):
    try:
        settings, user = _require_current_user(request, db)
        plan = svc_resolve_user_plan(db, user, settings=settings)
        item = svc_add_stock(
            db,
            user=user,
            stock_code=body.stock_code,
            stock_name=body.stock_name,
            plan=plan,
        )
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("watchlist add failed")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "添加自选股失败，请稍后重试"},
        )
    return {"stock": _serialize_watchlist_item(item)}


@router.put("/watchlist", summary="批量设置自选股（全量替换）")
async def account_set_watchlist(
    request: Request,
    body: WatchlistSetRequest,
    db: Session = Depends(get_db),
):
    try:
        settings, user = _require_current_user(request, db)
        plan = svc_resolve_user_plan(db, user, settings=settings)
        stock_codes = [
            (s.get("stockCode") or s.get("stock_code") or "").strip()
            for s in body.stocks
            if isinstance(s, dict)
        ]
        stock_names = {
            (s.get("stockCode") or s.get("stock_code") or "").strip(): (
                s.get("stockName") or s.get("stock_name") or ""
            ).strip()
            for s in body.stocks
            if isinstance(s, dict)
        }
        items = svc_set_watchlist(
            db,
            user=user,
            stock_codes=stock_codes,
            stock_names=stock_names,
            plan=plan,
        )
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("watchlist set failed")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "更新自选股失败，请稍后重试"},
        )
    return {
        "stocks": [_serialize_watchlist_item(i) for i in items],
        "count": len(items),
        "maxStocks": plan.max_stocks,
    }


@router.delete("/watchlist/{stock_code}", summary="删除一只自选股")
async def account_remove_watchlist(
    stock_code: str,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        _, user = _require_current_user(request, db)
        deleted = svc_remove_stock(db, user_id=user.id, stock_code=stock_code)
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("watchlist remove failed")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "删除自选股失败，请稍后重试"},
        )
    if not deleted:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": f"自选股 {stock_code} 不存在"},
        )
    return Response(status_code=204)


# ============================================================
# Notification Preferences (Phase 3) - per-user 通知偏好
# ============================================================


def _serialize_prefs(prefs) -> dict:
    return {
        "dailyPushEnabled": prefs.daily_push_enabled,
        "emailEnabled": prefs.email_enabled,
        "webhookUrl": prefs.webhook_url,
        "webhookType": prefs.webhook_type,
    }


@router.get("/notification-prefs", summary="获取当前用户通知偏好")
async def account_get_notification_prefs(request: Request, db: Session = Depends(get_db)):
    try:
        _, user = _require_current_user(request, db)
    except UserError as exc:
        return _user_error_response(exc)

    prefs = svc_get_prefs(db, user_id=user.id)
    db.commit()
    return {"prefs": _serialize_prefs(prefs)}


@router.patch("/notification-prefs", summary="更新当前用户通知偏好")
async def account_update_notification_prefs(
    request: Request,
    body: NotificationPrefsUpdateRequest,
    db: Session = Depends(get_db),
):
    try:
        settings, user = _require_current_user(request, db)
        plan = svc_resolve_user_plan(db, user, settings=settings)
        prefs = svc_update_prefs(
            db,
            user_id=user.id,
            daily_push_enabled=body.daily_push_enabled,
            email_enabled=body.email_enabled,
            webhook_url=body.webhook_url,
            webhook_type=body.webhook_type,
            clear_webhook=body.clear_webhook,
            can_webhook=plan.can_webhook,
        )
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("notification prefs update failed")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "更新通知偏好失败，请稍后重试"},
        )
    return {"prefs": _serialize_prefs(prefs)}


def _render_unsubscribe_page(*, success: bool, message: str) -> HTMLResponse:
    color = "#059669" if success else "#dc2626"
    title = "退订成功" if success else "退订失败"
    status_code = 200 if success else 400
    safe_msg = (message or "").replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} - DSA</title>
</head>
<body style="margin:0;padding:0;background:#f5f6f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Microsoft YaHei',sans-serif;color:#1f2937;">
  <div style="max-width:480px;margin:80px auto;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.06);padding:32px;text-align:center;">
    <h1 style="margin:0 0 12px 0;font-size:22px;color:{color};">{title}</h1>
    <p style="margin:0 0 24px 0;font-size:14px;line-height:1.6;color:#374151;">{safe_msg}</p>
    <p style="margin:0;font-size:13px;color:#6b7280;">如需重新开启推送，请登录 DSA 进入「账户设置 → 通知偏好」。</p>
  </div>
</body>
</html>
"""
    return HTMLResponse(content=html, status_code=status_code)


# ============================================================
# Account Deletion (Phase 6 PIPL)
# ============================================================


@router.post("/deletion", summary="申请注销账号（进入 7 天冷静期）")
async def account_request_deletion(
    request: Request,
    db: Session = Depends(get_db),
):
    """发起注销申请。

    - 立即撤销所有 session（用户强制下线）。
    - 进入 7 天冷静期，冷静期内可登录取消。
    - 冷静期到期后调度器将账号软删（status='deleted'），
      30 天后物理清除个人数据（保留订单/发票 5 年）。
    """
    try:
        _, user = _require_current_user(request, db)
    except UserError as exc:
        return _user_error_response(exc)

    try:
        svc_request_deletion(
            db,
            user=user,
            ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("account deletion request failed for user %s", user.id)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "申请注销失败，请稍后重试"},
        )

    response = Response(status_code=204)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response


@router.delete("/deletion", summary="取消注销申请（冷静期内）")
async def account_cancel_deletion(
    request: Request,
    db: Session = Depends(get_db),
):
    """在冷静期内取消注销申请。"""
    try:
        _, user = _require_current_user(request, db)
    except UserError as exc:
        return _user_error_response(exc)

    try:
        svc_cancel_deletion(
            db,
            user=user,
            ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("cancel deletion failed for user %s", user.id)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "取消注销失败，请稍后重试"},
        )

    return {"ok": True, "message": "注销申请已取消"}


@router.get("/deletion", summary="查询注销申请状态")
async def account_deletion_status(
    request: Request,
    db: Session = Depends(get_db),
):
    """返回当前用户的注销申请状态。"""
    try:
        _, user = _require_current_user(request, db)
    except UserError as exc:
        return _user_error_response(exc)

    requested_at = getattr(user, "deletion_requested_at", None)
    return {
        "hasPendingDeletion": requested_at is not None,
        "deletionRequestedAt": requested_at.isoformat() if requested_at else None,
        "coolingOffDays": _DELETION_COOLING_OFF_DAYS,
    }


# ============================================================
# Personal Data Export (Phase 6 PIPL)
# ============================================================


@router.post("/data-export", summary="申请导出个人数据（发至注册邮箱）")
async def account_request_data_export(
    request: Request,
    db: Session = Depends(get_db),
):
    """收集并发送用户个人数据 JSON 至注册邮箱。

    MVP：直接附于邮件正文。
    二期：生成签名临时 URL（OSS / S3）。
    """
    try:
        _, user = _require_current_user(request, db)
    except UserError as exc:
        return _user_error_response(exc)

    try:
        svc_request_data_export(
            db,
            user=user,
            ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except UserError as exc:
        db.rollback()
        return _user_error_response(exc)
    except Exception:
        db.rollback()
        logger.exception("data export failed for user %s", user.id)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "数据导出失败，请稍后重试"},
        )

    return {"ok": True, "message": f"数据导出已发送至 {user.email}，请查收邮件"}


@router.get(
    "/notification-prefs/unsubscribe",
    summary="一键退订（无需登录，通过签名 token 校验）",
)
async def account_unsubscribe(
    request: Request,
    token: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """点击邮件中的「一键退订」链接后命中此端点。

    - ``ACTION_DAILY`` (默认): 关闭 ``daily_push_enabled`` (停止每日推送)。
    - ``ACTION_EMAIL``: 同时关闭 ``email_enabled`` (所有邮件)。
    """
    claim = verify_unsubscribe_token(token)
    if claim is None:
        return _render_unsubscribe_page(
            success=False,
            message="退订链接无效或已过期。请重新登录后在通知偏好中关闭推送。",
        )

    try:
        if claim.action == ACTION_EMAIL:
            svc_update_prefs(
                db,
                user_id=claim.user_id,
                daily_push_enabled=False,
                email_enabled=False,
            )
            human_action = "邮件推送"
        else:
            svc_update_prefs(
                db,
                user_id=claim.user_id,
                daily_push_enabled=False,
            )
            human_action = "每日推送"
        _commit_or_rollback(db)
    except UserError as exc:
        db.rollback()
        return _render_unsubscribe_page(success=False, message=exc.message)
    except Exception:
        db.rollback()
        logger.exception("unsubscribe failed for user_id=%s", claim.user_id)
        return _render_unsubscribe_page(
            success=False,
            message="服务暂时不可用，请稍后再试。",
        )

    write_audit_log(
        db,
        "notification.unsubscribe",
        user_id=int(claim.user_id),
        target_user_id=int(claim.user_id),
        target_ref=claim.action,
        ip=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        detail={"action": claim.action},
    )

    return _render_unsubscribe_page(
        success=True,
        message=f"已为您关闭{human_action}。如需再次开启，请登录后在「账户设置 → 通知偏好」中切换。",
    )
