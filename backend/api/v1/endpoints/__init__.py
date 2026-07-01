# -*- coding: utf-8 -*-
"""Endpoint module exports for API v1.

具体路由挂载在 ``api.v1.router`` 中完成；这里保留 endpoint 模块的稳定导出名，
并按需导入，避免单独导入某个 endpoint 时提前加载整站业务模块。
"""

from importlib import import_module

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
    "notices",
    "portfolio",
    "research_reports",
    "stock_selection",
    "stocks",
    "system_config",
    "usage",
}


def __getattr__(name):
    """Lazily import endpoint modules by public export name."""
    if name in _ENDPOINT_MODULES:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
    "notices",
    "portfolio",
    "research_reports",
    "stock_selection",
    "stocks",
    "system_config",
    "usage",
]
