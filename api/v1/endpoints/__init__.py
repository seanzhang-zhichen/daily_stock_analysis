# -*- coding: utf-8 -*-
"""
===================================
API v1 Endpoints 模块初始化
===================================

职责：
1. 声明所有 endpoint 路由模块
"""

from api.v1.endpoints import (
    account,
    agent,
    alerts,
    analysis,
    auth,
    backtest,
    billing,
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
    "health",
    "history",
    "notices",
    "portfolio",
    "stocks",
    "system_config",
    "usage",
]
