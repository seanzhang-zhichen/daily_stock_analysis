# -*- coding: utf-8 -*-
"""Aggregate all API v1 endpoint routers under ``/api/v1``.

每个业务域在 ``api.v1.endpoints`` 下维护自己的 router，本文件只负责统一前缀
和 OpenAPI tag。新增 endpoint 模块时应在这里显式 include，便于审查公开 API
面是否发生变化。
"""

from fastapi import APIRouter

from api.v1.endpoints import (
    account,
    admin,
    agent,
    alerts,
    analysis,
    auth,
    backtest,
    billing,
    credits,
    history,
    notices,
    portfolio,
    research_reports,
    stocks,
    system_config,
    usage,
)

# v1 版本主路由；所有下面 include 的 prefix 都会自动拼到 /api/v1 之后。
router = APIRouter(prefix="/api/v1")

router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Auth"]
)

router.include_router(
    account.router,
    prefix="/account",
    tags=["Account"]
)

router.include_router(
    agent.router,
    prefix="/agent",
    tags=["Agent"]
)

router.include_router(
    analysis.router,
    prefix="/analysis",
    tags=["Analysis"]
)

router.include_router(
    history.router,
    prefix="/history",
    tags=["History"]
)

router.include_router(
    stocks.router,
    prefix="/stocks",
    tags=["Stocks"]
)

router.include_router(
    backtest.router,
    prefix="/backtest",
    tags=["Backtest"]
)

router.include_router(
    system_config.router,
    prefix="/system",
    tags=["SystemConfig"]
)

router.include_router(
    usage.router,
    prefix="/usage",
    tags=["Usage"]
)

router.include_router(
    portfolio.router,
    prefix="/portfolio",
    tags=["Portfolio"]
)

router.include_router(
    alerts.router,
    prefix="/alerts",
    tags=["Alerts"]
)

router.include_router(
    billing.router,
    prefix="/billing",
    tags=["Billing"]
)

router.include_router(
    credits.router,
    prefix="/credits",
    tags=["Credits"]
)

router.include_router(
    admin.router,
    prefix="/admin",
    tags=["Admin"]
)

router.include_router(
    notices.router,
    prefix="/notices",
    tags=["Notices"]
)

router.include_router(
    research_reports.router,
    prefix="/research-reports",
    tags=["ResearchReports"]
)
