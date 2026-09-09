# -*- coding: utf-8 -*-
"""可复用的 FastAPI 依赖提供者（dependency providers）。

集中放置请求级资源获取逻辑，包括数据库 Session、全局配置、当前登录用户以及
应用生命周期内共享的服务实例。把这些依赖集中维护，可以让 endpoint 函数保持薄
而明确，也便于在测试时按需替换依赖实现。

主要导出：
- ``get_db``：请求级 SQLAlchemy Session
- ``get_config_dep`` / ``get_database_manager``：进程级单例
- ``get_optional_current_user`` / ``get_current_user`` / ``get_admin_user``：登录态解析
- ``get_system_config_service``：系统配置服务实例
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
    """产出一个请求级（request-scoped）的 SQLAlchemy Session，并在结束后关闭。

    FastAPI 会在 endpoint 执行完成后继续推进 generator，因此 ``finally`` 中的
    ``session.close()`` 能覆盖正常返回、``HTTPException`` 以及未处理异常三种
    路径。调用方不应把该 Session 持久保存到后台任务或全局变量中。

    Yields:
        Session: 与本次请求绑定的数据库会话实例。
    """
    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        yield session
    finally:
        # 通过 finally 保证连接一定会归还到连接池，避免请求结束后的连接泄漏
        session.close()


def get_config_dep() -> Config:
    """返回进程级的全局配置对象，供依赖注入使用。

    Returns:
        Config: 通过 ``src.config.get_config`` 获取的全局 ``Config`` 实例。
    """
    return get_config()


def get_database_manager() -> DatabaseManager:
    """返回数据库管理器单例，供需要工厂方法的业务服务使用。

    Returns:
        DatabaseManager: 进程内唯一的 ``DatabaseManager`` 单例。
    """
    return DatabaseManager.get_instance()


def get_optional_current_user(request: Request) -> Optional[AppUser]:
    """返回本次请求绑定的 To C 用户，未登录则返回 ``None``。

    优先读取 :attr:`request.state.user`（由 ``AuthMiddleware`` 在用户态路径上
    预填充），避免重复查询数据库；当中间件被豁免的 endpoint（如
    ``/api/v1/account/*``）命中时，再回退到按 Cookie 重新解析会话。

    Args:
        request: 当前的 FastAPI 请求对象。

    Returns:
        Optional[AppUser]: 已登录则返回用户对象，否则返回 ``None``。
    """
    cached = getattr(request.state, "user", None)
    if cached is not None:
        # 命中 AuthMiddleware 预填充结果，跳过 DB 查询
        return cached
    cookie_val = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie_val:
        # 无会话 Cookie 直接判定为匿名，省一次 DB 往返
        return None
    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        user = resolve_session(session, cookie_val)
        if user is not None:
            # 回填到 request.state，便于同一请求后续依赖直接复用
            request.state.user = user
        return user
    finally:
        session.close()


def get_current_user(request: Request) -> AppUser:
    """返回当前登录用户，未登录则抛出 401。

    适用于需要强制登录态的 endpoint。

    Args:
        request: 当前的 FastAPI 请求对象。

    Returns:
        AppUser: 当前登录的用户对象。

    Raises:
        HTTPException: 当用户未登录时抛出 401。
    """
    # 复用可选用户依赖，避免重复实现 Cookie -> User 的解析逻辑
    user = get_optional_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "请先登录"})
    return user


def get_admin_user(request: Request) -> AppUser:
    """返回当前管理员用户，否则抛出对应的鉴权异常。

    用于 ``/api/v1/admin/*`` 的运营后台 endpoint 鉴权。未登录返回 401，已登录
    但非 admin 返回 403，便于前端区分"需要登录"和"权限不足"两种场景。

    Args:
        request: 当前的 FastAPI 请求对象。

    Returns:
        AppUser: 当前已登录且具备管理员权限的用户对象。

    Raises:
        HTTPException: 未登录时抛出 401；非管理员时抛出 403。
    """
    user = get_optional_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "请先登录"})
    if not bool(getattr(user, "is_admin", False)):
        # getattr 兜底避免老用户对象缺字段；bool() 把 None/0 一并视为非管理员
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "需要平台管理员权限"},
        )
    return user


def get_system_config_service(request: Request) -> "SystemConfigService":
    """获取应用生命周期内共享的 SystemConfigService 实例。

    正常情况下，``api.app.app_lifespan`` 会在启动时创建该服务一次并挂到
    ``app.state`` 上；当 lifespan 钩子未执行（例如测试或手动构造的 FastAPI
    应用）时，本函数会兜底创建一份实例，保证依赖始终可用。

    Args:
        request: 当前的 FastAPI 请求对象，用于访问 ``request.app.state``。

    Returns:
        SystemConfigService: 单例的系统配置服务实例。
    """
    service = getattr(request.app.state, "system_config_service", None)
    if service is None:
        # 兜底分支：lifespan 未执行时按需导入并创建实例，同时回写到 state 避免下次重复构造
        from src.services.system_config_service import SystemConfigService

        service = SystemConfigService()
        request.app.state.system_config_service = service
    return service


def get_runtime_scheduler_service(request: Request) -> "RuntimeSchedulerService":
    """Return the app-lifecycle runtime scheduler, creating a test fallback if needed."""
    service = getattr(request.app.state, "runtime_scheduler", None)
    if service is None:
        from src.services.runtime_scheduler import RuntimeSchedulerService

        service = RuntimeSchedulerService()
        request.app.state.runtime_scheduler = service
    return service
