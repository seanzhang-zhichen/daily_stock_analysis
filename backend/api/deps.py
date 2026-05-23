# -*- coding: utf-8 -*-
"""
===================================
API 依赖注入模块
===================================

职责：
1. 提供数据库 Session 依赖
2. 提供配置依赖
3. 提供服务层依赖
"""

from typing import Generator, Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from src.storage import AppUser, DatabaseManager
from src.config import get_config, Config
from src.services.system_config_service import SystemConfigService
from src.users.config import SESSION_COOKIE_NAME
from src.users.sessions import resolve_session


def get_db() -> Generator[Session, None, None]:
    """
    获取数据库 Session 依赖
    
    使用 FastAPI 依赖注入机制，确保请求结束后自动关闭 Session
    
    Yields:
        Session: SQLAlchemy Session 对象
        
    Example:
        @router.get("/items")
        async def get_items(db: Session = Depends(get_db)):
            ...
    """
    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def get_config_dep() -> Config:
    """
    获取配置依赖
    
    Returns:
        Config: 配置单例对象
    """
    return get_config()


def get_database_manager() -> DatabaseManager:
    """
    获取数据库管理器依赖
    
    Returns:
        DatabaseManager: 数据库管理器单例对象
    """
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
    """Strict variant: raises 401 when no user is bound to this request."""
    user = get_optional_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "请先登录"})
    return user


def get_admin_user(request: Request) -> AppUser:
    """Strict variant: 要求已登录且 ``is_admin=True``。

    用于 ``/api/v1/admin/*`` 的运营后台 endpoint 鉴权。
    未登录返回 401, 已登录但非 admin 返回 403。
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


def get_system_config_service(request: Request) -> SystemConfigService:
    """Get app-lifecycle shared SystemConfigService instance."""
    service = getattr(request.app.state, "system_config_service", None)
    if service is None:
        service = SystemConfigService()
        request.app.state.system_config_service = service
    return service
