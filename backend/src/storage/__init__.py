# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 存储层
===================================

职责：
1. 管理 SQLite 数据库连接（单例模式）
2. 定义 ORM 数据模型
3. 提供数据存取接口
4. 实现智能更新逻辑（断点续传）

为减小单文件体积, 拆分为多个子模块：

- ``base``                共享的 SQLAlchemy ``Base``
- ``models.*``            按业务域拆分的 ORM 模型
- ``manager._base``       ``DatabaseManager`` 的基础设施层
- ``manager.<feature>``   各业务 Mixin
- ``manager.manager``     最终装配的 ``DatabaseManager`` 与便捷函数

本 ``__init__`` 统一 re-export, 保持外部 ``from src.storage import X`` 调用兼容。
"""

from src.storage.base import Base
from src.storage.manager import DatabaseManager, get_db, persist_llm_usage
from src.storage.models import (
    AlertNotificationRecord,
    AlertRuleRecord,
    AlertTriggerRecord,
    AnalysisHistory,
    AppAuditLog,
    AppInvoice,
    AppOrder,
    AppPaymentEvent,
    AppPlatformSetting,
    AppPlan,
    AppPlanReminder,
    AppReconciliationDiff,
    AppReconciliationReport,
    AppRedeemCode,
    AppRefund,
    AppSubscription,
    AppUser,
    AppUserConsent,
    AppUserEmailVerification,
    AppUserNotificationPref,
    AppUserSession,
    AppUserUsageCounter,
    AppUserWatchlist,
    AppGrowthEvent,
    AppNotice,
    BacktestResult,
    BacktestSummary,
    ConversationMessage,
    FundamentalSnapshot,
    LLMUsage,
    NewsIntel,
    PortfolioAccount,
    PortfolioCashLedger,
    PortfolioCorporateAction,
    PortfolioDailySnapshot,
    PortfolioFxRate,
    PortfolioPosition,
    PortfolioPositionLot,
    PortfolioTrade,
    StockDaily,
    StockIndexEntry,
    StockIndexMeta,
)

__all__ = [
    # base
    "Base",
    # manager
    "DatabaseManager",
    "get_db",
    "persist_llm_usage",
    # core models
    "StockDaily",
    "NewsIntel",
    "FundamentalSnapshot",
    "AnalysisHistory",
    "StockIndexEntry",
    "StockIndexMeta",
    # backtest
    "BacktestResult",
    "BacktestSummary",
    # portfolio
    "PortfolioAccount",
    "PortfolioTrade",
    "PortfolioCashLedger",
    "PortfolioCorporateAction",
    "PortfolioPosition",
    "PortfolioPositionLot",
    "PortfolioDailySnapshot",
    "PortfolioFxRate",
    # conversation / llm
    "ConversationMessage",
    "LLMUsage",
    # alert
    "AlertRuleRecord",
    "AlertTriggerRecord",
    "AlertNotificationRecord",
    # app (To C)
    "AppUser",
    "AppUserSession",
    "AppUserEmailVerification",
    "AppUserUsageCounter",
    "AppPlan",
    "AppPlatformSetting",
    "AppSubscription",
    "AppRedeemCode",
    "AppUserWatchlist",
    "AppUserNotificationPref",
    "AppOrder",
    "AppPaymentEvent",
    "AppRefund",
    "AppInvoice",
    "AppUserConsent",
    "AppReconciliationDiff",
    "AppReconciliationReport",
    "AppPlanReminder",
    "AppAuditLog",
    "AppGrowthEvent",
    "AppNotice",
]
