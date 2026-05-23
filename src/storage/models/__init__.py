# -*- coding: utf-8 -*-
"""数据模型聚合入口。

按业务域拆分到子模块，再统一在此 re-export，方便其他模块通过
``from src.storage.models import StockDaily`` 或 ``from src.storage import StockDaily``
两种方式访问。
"""

from src.storage.models.alert import (
    AlertNotificationRecord,
    AlertRuleRecord,
    AlertTriggerRecord,
)
from src.storage.models.app import (
    AppAuditLog,
    AppGrowthEvent,
    AppInvoice,
    AppNotice,
    AppOrder,
    AppPaymentEvent,
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
)
from src.storage.models.backtest import BacktestResult, BacktestSummary
from src.storage.models.conversation import ConversationMessage, LLMUsage
from src.storage.models.core import (
    AnalysisHistory,
    FundamentalSnapshot,
    NewsIntel,
    StockDaily,
)
from src.storage.models.portfolio import (
    PortfolioAccount,
    PortfolioCashLedger,
    PortfolioCorporateAction,
    PortfolioDailySnapshot,
    PortfolioFxRate,
    PortfolioPosition,
    PortfolioPositionLot,
    PortfolioTrade,
)

__all__ = [
    # core
    "StockDaily",
    "NewsIntel",
    "FundamentalSnapshot",
    "AnalysisHistory",
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
