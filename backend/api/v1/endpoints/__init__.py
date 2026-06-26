# -*- coding: utf-8 -*-
"""Endpoint module exports for API v1.

具体路由挂载在 ``api.v1.router`` 中完成；这里集中导入 endpoint 模块，方便路由
聚合和测试代码使用稳定模块名引用。
"""

from api.v1.endpoints import (
    account,
    agent,
    alerts,
    analysis,
    auth,
    backtest,
    billing,
    credits,
    health,
    history,
    notices,
    portfolio,
    stocks,
    system_config,
    usage,
)

__all__ = [
    "account",
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
    "stocks",
    "system_config",
    "usage",
]
