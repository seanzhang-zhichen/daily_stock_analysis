# -*- coding: utf-8 -*-
"""数据模型聚合入口。

按业务域拆分到子模块，再统一在此 re-export，方便其他模块通过
``from src.storage.models import StockDaily`` 或 ``from src.storage import StockDaily``
两种方式访问。

导入顺序与分组逻辑：
- alert: 告警规则与触发记录
- app: C 端用户体系（用户、订单、订阅、积分等）
- backtest: 回测结果与汇总
- conversation: 对话历史与 LLM 用量
- decision_signal / decision_signal_outcome / decision_signal_feedback / skill_opinion: AI 决策信号与反馈
- core: 核心业务（日线、新闻、分析历史、选股、股票索引）
- portfolio: 投资组合
"""

from src.storage.models.alert import (
    AlertCooldownRecord,
    AlertNotificationRecord,
    AlertRuleRecord,
    AlertTriggerRecord,
)
from src.storage.models.app import (
    AppAuditLog,
    AppCreditLedger,
    AppCreditOrder,
    AppCreditPackage,
    AppCreditPaymentEvent,
    AppGrowthEvent,
    AppInvoice,
    AppNotice,
    AppOrder,
    AppPaymentEvent,
    AppPlatformSetting,
    AppPlan,
    AppPlanReminder,
    AppReconciliationDiff,
    AppReconciliationReport,
    AppRedeemCode,
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
    AppResearchReport,
    AppResearchReportComment,
    AppResearchReportPurchase,
    AppResearchReportReaction,
)
from src.storage.models.backtest import BacktestResult, BacktestSummary
from src.storage.models.conversation import ConversationMessage, ConversationSessionState, ConversationSummary, LLMUsage
from src.storage.models.decision_signal import DecisionSignalRecord
from src.storage.models.decision_signal_outcome import DecisionSignalOutcomeRecord
from src.storage.models.decision_signal_feedback import DecisionSignalFeedbackRecord
from src.storage.models.skill_opinion import SkillOpinionOutcomeRecord, SkillOpinionSampleRecord
from src.storage.models.core import (
    AnalysisHistory,
    FundamentalSnapshot,
    NewsIntel,
    IntelligenceSource,
    IntelligenceItem,
    INTELLIGENCE_ITEM_NULL_SCOPE_VALUE,
    ScreeningRun,
    StockDaily,
    StockIndexEntry,
    StockIndexMeta,
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
    # core: 核心业务数据模型
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
    # backtest: 回测相关模型
    "BacktestResult",
    "BacktestSummary",
    # portfolio: 投资组合相关模型
    "PortfolioAccount",
    "PortfolioTrade",
    "PortfolioCashLedger",
    "PortfolioCorporateAction",
    "PortfolioPosition",
    "PortfolioPositionLot",
    "PortfolioDailySnapshot",
    "PortfolioFxRate",
    # conversation / llm: 对话历史与 LLM 用量模型
    "ConversationMessage",
    "ConversationSessionState",
    "ConversationSummary",
    "LLMUsage",
    # decision signals: 决策信号与反馈模型
    "DecisionSignalRecord",
    "DecisionSignalOutcomeRecord",
    "DecisionSignalFeedbackRecord",
    "SkillOpinionSampleRecord",
    "SkillOpinionOutcomeRecord",
    # alert: 告警规则与通知模型
    "AlertRuleRecord",
    "AlertTriggerRecord",
    "AlertNotificationRecord",
    "AlertCooldownRecord",
    # app (To C): C 端用户体系相关模型
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
