# -*- coding: utf-8 -*-
"""Auth middleware: protect /api/v1/* with multi-user sessions."""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.users.config import SESSION_COOKIE_NAME
from src.storage import DatabaseManager
from src.users.sessions import resolve_session

logger = logging.getLogger(__name__)

EXEMPT_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/status",
    "/api/v1/account/status",
    "/api/v1/account/register",
    "/api/v1/account/login",
    "/api/v1/account/logout",
    "/api/v1/account/verify-email",
    "/api/v1/account/request-password-reset",
    "/api/v1/account/reset-password",
    # Phase 3: 一键退订链接出现在邮件中, 无需登录即可关闭推送
    "/api/v1/account/notification-prefs/unsubscribe",
    # Phase 2: 套餐目录在落地页 / 注册流引导可见, 不需要登录
    "/api/v1/billing/plans",
    # Phase 6: 增长埋点可匿名上报
    "/api/v1/usage/events",
    # Phase 6: 公告中心公开接口（落地页 / 已登录均可访问）
    "/api/v1/notices",
    "/api/v1/notices/unread-count",
    # Phase 6: 协议静态页
    "/api/v1/legal/terms",
    "/api/v1/legal/privacy",
    "/api/v1/legal/risk-disclosure",
    "/api/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
})


def _path_exempt(path: str) -> bool:
    """Check if path is exempt from auth."""
    normalized = path.rstrip("/") or "/"
    return normalized in EXEMPT_PATHS


def _resolve_user_session(request: Request):
    """Lookup the C 端 user bound to the request, if any.

    Returns the :class:`AppUser` ORM row when found, ``None`` otherwise. The
    user is stashed on ``request.state.user`` so downstream dependencies can
    read it without re-querying the DB.
    """
    cookie_val = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie_val:
        return None
    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        return resolve_session(session, cookie_val)
    finally:
        session.close()


class AuthMiddleware(BaseHTTPMiddleware):
    """Require a valid multi-user session for /api/v1/* business endpoints."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ):
        path = request.url.path
        if _path_exempt(path):
            return await call_next(request)

        if not path.startswith("/api/v1/"):
            return await call_next(request)

        current_user = _resolve_user_session(request)
        if current_user is None:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "Login required",
                },
            )

        request.state.user = current_user
        return await call_next(request)


def add_auth_middleware(app):
    """Add auth middleware to protect API routes.

    The middleware is always registered; whether auth is enforced is determined
    at request time by is_auth_enabled() so the decision stays consistent across
    any runtime configuration reload.
    """
    app.add_middleware(AuthMiddleware)
