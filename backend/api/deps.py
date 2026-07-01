# -*- coding: utf-8 -*-
"""Reusable FastAPI dependency providers.

本模块集中放置请求级资源获取逻辑，包括数据库 Session、全局配置、当前登录
用户与应用生命周期内共享的服务实例。把这些依赖集中维护，可以让 endpoint
函数保持薄而明确，也便于测试时替换依赖。
"""

from typing import TYPE_CHECKING, Generator, Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from src.storage import AppUser, DatabaseManager
from src.config import get_config, Config
from src.users.config import SESSION_COOKIE_NAME
from src.users.sessions import resolve_session

if TYPE_CHECKING:
    from src.services.system_config_service import SystemConfigService


def get_db() -> Generator[Session, None, None]:
    """Yield a request-scoped SQLAlchemy Session and close it afterwards.

    FastAPI 会在 endpoint 执行完成后继续推进 generator，因此 ``finally``
    中的 ``session.close()`` 能覆盖正常返回、HTTPException 和未处理异常。
    调用方不应把该 Session 持久保存到后台任务或全局变量。
    """
    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def get_config_dep() -> Config:
    """Return the process-wide configuration object for dependency injection."""
    return get_config()


def get_database_manager() -> DatabaseManager:
    """Return the database manager singleton for services that need factories."""
    return DatabaseManager.get_instance()


def get_optional_current_user(request: Request) -> Optional[AppUser]:
    """Return the To C user bound to this request, or ``None`` if not logged in.

    Looks up :attr:`request.state.user` first (already populated by
    :class:`api.middlewares.auth.AuthMiddleware` for the user-mode path) so
    we don't hit the DB twice. Falls back to a fresh DB lookup so endpoints
    that are exempt from the middleware (e.g. ``/api/v1/account/*``) can also
    discover the current user.
    """
    cached = getattr(request.state, "user", None)
    if cached is not None:
        return cached
    cookie_val = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie_val:
        return None
    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        user = resolve_session(session, cookie_val)
        if user is not None:
            request.state.user = user
        return user
    finally:
        session.close()


def get_current_user(request: Request) -> AppUser:
    """Return the current user, raising 401 for anonymous requests."""
    user = get_optional_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "请先登录"})
    return user


def get_admin_user(request: Request) -> AppUser:
    """Return the current admin user or raise the matching auth error.

    用于 ``/api/v1/admin/*`` 的运营后台 endpoint 鉴权。
    未登录返回 401，已登录但非 admin 返回 403，便于前端区分“需要登录”和“权限不足”。
    """
    user = get_optional_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "请先登录"})
    if not bool(getattr(user, "is_admin", False)):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "需要平台管理员权限"},
        )
    return user


def get_system_config_service(request: Request) -> "SystemConfigService":
    """Get the app-lifecycle shared SystemConfigService instance.

    ``api.app.app_lifespan`` normally creates this service once and stores it
    on ``app.state``. The fallback keeps tests and manually constructed FastAPI
    apps usable even when lifespan hooks were not executed.
    """
    service = getattr(request.app.state, "system_config_service", None)
    if service is None:
        from src.services.system_config_service import SystemConfigService

        service = SystemConfigService()
        request.app.state.system_config_service = service
    return service
