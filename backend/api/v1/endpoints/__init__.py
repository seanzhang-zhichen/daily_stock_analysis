# -*- coding: utf-8 -*-
"""API v1 endpoint 模块的统一导出层。

具体路由挂载在 ``api.v1.router`` 中完成；这里保留 endpoint 模块的稳定导出名，
并按需导入，避免单独导入某个 endpoint 时提前加载整站业务模块。

新增 endpoint 流程：

1. 在本目录新增 ``xxx.py``；
2. 在 ``_ENDPOINT_MODULES`` 与 ``__all__`` 中追加 ``"xxx"``；
3. 在 ``api.v1.router`` 中调用 ``include_router`` 挂载。
"""

from importlib import import_module

# endpoint 模块白名单——只有出现在这里的模块才允许被惰性加载。
_ENDPOINT_MODULES = {
    "account",
    "admin",
    "agent",
    "alerts",
    "analysis",
    "auth",
    "backtest",
    "billing",
    "credits",
    "health",
    "history",
    "intelligence",
    "notices",
    "portfolio",
    "research_reports",
    "screening",
    "stock_selection",
    "stocks",
    "system_config",
    "usage",
}


def __getattr__(name):
    """PEP 562 风格的惰性属性加载：按需 import endpoint 模块。

    Args:
        name: 调用方访问的属性名（应为 endpoint 模块名）。

    Returns:
        对应的 endpoint 模块对象；同时被缓存到 ``globals()``，下次访问直接返回。

    Raises:
        AttributeError: ``name`` 不在白名单时抛出。
    """
    if name in _ENDPOINT_MODULES:
        module = import_module(f"{__name__}.{name}")
        # 缓存到 globals，下次访问不再走 import_module，缩短调用栈
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# 对外公开的 endpoint 模块集合，配合 ``__getattr__`` 实现"用哪个就导入哪个"
__all__ = [
    "account",
    "admin",
    "agent",
    "alerts",
    "analysis",
    "auth",
    "backtest",
    "billing",
    "credits",
    "health",
    "history",
    "intelligence",
    "notices",
    "portfolio",
    "research_reports",
    "screening",
    "stock_selection",
    "stocks",
    "system_config",
    "usage",
]
