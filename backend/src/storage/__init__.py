# -*- coding: utf-8 -*-
"""
存储层（Storage）公共入口。

为减小单文件体积, ORM 模型与 ``DatabaseManager`` 被拆分为多个子模块，
本文件统一 re-export 所有公共符号，保持外部 ``from src.storage import X``
调用方式不变。

子模块拆分：

- ``base``                共享的 SQLAlchemy ``Base``
- ``models.*``            按业务域拆分的 ORM 模型
- ``manager._base``       ``DatabaseManager`` 的基础设施层
- ``manager.<feature>``   各业务 Mixin（日线、新闻、对话、LLM 用量等）
- ``manager.manager``     最终装配的 ``DatabaseManager`` 与便捷函数

``DatabaseManager`` / ``get_db`` / ``persist_llm_usage`` 通过 ``__getattr__``
按需懒加载，避免仅 ``import`` 模型时触发数据库初始化。
"""

from src.storage.base import Base
from src.storage.models import (
    AlertCooldownRecord,
    AlertNotificationRecord,
    AlertRuleRecord,
    AlertTriggerRecord,
    AnalysisHistory,
    AppAuditLog,
    AppCreditLedger,
    AppCreditOrder,
    AppCreditPackage,
    AppCreditPaymentEvent,
    AppInvoice,
    AppOrder,
    AppPaymentEvent,
    AppPlatformSetting,
    AppPlan,
    AppPlanReminder,
    AppReconciliationDiff,
    AppReconciliationReport,
    AppRedeemCode,
    AppResearchReport,
    AppResearchReportComment,
    AppResearchReportPurchase,
    AppResearchReportReaction,
    AppUserReferral,
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
    ConversationSessionState,
    ConversationSummary,
    DecisionSignalRecord,
    DecisionSignalOutcomeRecord,
    DecisionSignalFeedbackRecord,
    SkillOpinionSampleRecord,
    SkillOpinionOutcomeRecord,
    FundamentalSnapshot,
    LLMUsage,
    NewsIntel,
    IntelligenceSource,
    IntelligenceItem,
    INTELLIGENCE_ITEM_NULL_SCOPE_VALUE,
    ScreeningRun,
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


def __getattr__(name):
    """按需导入 DatabaseManager 相关对象，避免导入模型时提前初始化数据库。"""
    if name in {"DatabaseManager", "get_db", "persist_llm_usage"}:
        from src.storage.manager import DatabaseManager, get_db, persist_llm_usage

        values = {
            "DatabaseManager": DatabaseManager,
            "get_db": get_db,
            "persist_llm_usage": persist_llm_usage,
        }
        globals().update(values)
        return values[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    "IntelligenceSource",
    "IntelligenceItem",
    "INTELLIGENCE_ITEM_NULL_SCOPE_VALUE",
    "FundamentalSnapshot",
    "AnalysisHistory",
    "ScreeningRun",
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
    "ConversationSessionState",
    "ConversationSummary",
    "LLMUsage",
    # decision signals
    "DecisionSignalRecord",
    "DecisionSignalOutcomeRecord",
    "DecisionSignalFeedbackRecord",
    "SkillOpinionSampleRecord",
    "SkillOpinionOutcomeRecord",
    # alert
    "AlertRuleRecord",
    "AlertTriggerRecord",
    "AlertNotificationRecord",
    "AlertCooldownRecord",
    # app (To C)
    "AppUser",
    "AppUserSession",
    "AppUserEmailVerification",
    "AppUserUsageCounter",
    "AppUserReferral",
    "AppCreditLedger",
    "AppCreditPackage",
    "AppCreditOrder",
    "AppCreditPaymentEvent",
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
    "AppResearchReport",
    "AppResearchReportPurchase",
    "AppResearchReportReaction",
    "AppResearchReportComment",
]
