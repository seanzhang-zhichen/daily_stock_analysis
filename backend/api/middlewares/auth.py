# -*- coding: utf-8 -*-
"""Authentication middleware for API business endpoints.

中间件只拦截 ``/api/v1/*`` 下需要登录的业务接口。公开接口、回调、健康检查、
OpenAPI 文档和登录注册链路通过 ``EXEMPT_PATHS`` 放行，以保证用户尚未建立
会话时仍能完成账号流程或被外部平台回调。
"""

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

# 这里使用精确路径白名单，避免把整段前缀误放开。确需放行一组资源时，
# 在 _path_exempt 中单独写带边界判断的前缀规则。
EXEMPT_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/status",
    "/api/v1/account/status",
    "/api/v1/account/register",
    "/api/v1/account/login",
    "/api/v1/account/logout",
    "/api/v1/account/verify-email",
    "/api/v1/account/request-email-verification",
    "/api/v1/account/request-password-reset",
    "/api/v1/account/reset-password",
    # Phase 3: 一键退订链接出现在邮件中, 无需登录即可关闭推送
    "/api/v1/account/notification-prefs/unsubscribe",
    # Phase 2: 套餐目录在落地页 / 注册流引导可见, 不需要登录
    "/api/v1/billing/plans",
    "/api/v1/credits/packages",
    "/api/v1/billing/callbacks/wechat",
    "/api/v1/billing/callbacks/alipay",
    # Phase 6: 增长埋点可匿名上报
    "/api/v1/usage/events",
    # Phase 6: 公告中心公开接口（落地页 / 已登录均可访问）
    "/api/v1/notices",
    "/api/v1/notices/unread-count",
    "/api/v1/stocks/search",
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
    """判断 ``path`` 是否可以绕过登录校验。

    使用 ``rstrip("/")`` 归一化路径，使 ``/foo`` 与 ``/foo/`` 行为一致；
    research report 公开访问需要按资源前缀放行，因此在此处单独处理，
    避免扩大 ``EXEMPT_PATHS`` 的匹配语义。
    """
    normalized = path.rstrip("/") or "/"
    if normalized == "/api/v1/research-reports" or normalized.startswith("/api/v1/research-reports/"):
        return True
    return normalized in EXEMPT_PATHS


def _resolve_user_session(request: Request):
    """解析绑定到当前请求的 To C 用户（若存在）。

    找到时返回 :class:`AppUser` ORM 行，否则返回 ``None``。同时把用户对象缓存到
    ``request.state.user``，下游依赖可直接读取而无需再次访问数据库。

    这里短生命周期地打开一个 SQLAlchemy 会话，是因为中间件先于 FastAPI 的依赖
    注入运行；ORM 对象仅在当前请求内使用，校验通过后立即写入
    ``request.state`` 缓存。
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
    """要求受保护 API 端点必须携带有效的多用户会话。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ):
        """在路由匹配前附加会话上下文或拒绝未授权请求。"""
        path = request.url.path
        if _path_exempt(path):
            return await call_next(request)

        # 静态文件、根路径、文档与历史遗留的非 v1 端点不在本中间件管理范围内，
        # 除非在上面 EXEMPT_PATHS 中显式登记。
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
    """在 FastAPI 应用上注册鉴权中间件。"""
    app.add_middleware(AuthMiddleware)
