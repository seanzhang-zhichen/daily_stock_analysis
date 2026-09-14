# -*- coding: utf-8 -*-
"""API v1 Pydantic 模型的集中导出模块。

本模块作为各业务域 Schema 的统一入口，提供便捷的集中导入能力。
实际模型定义仍按业务域拆分在同级文件中（如 common.py、analysis.py 等）。

设计目的：
- 兼容历史代码中 ``from api.v1.schemas import Foo`` 的写法；
- 让测试代码可以从一个入口获取所有公开的契约模型；
- 避免在多处重复导入分散的模块。
"""

# 从 common.py 导入基础响应模型
from api.v1.schemas.common import (
    RootResponse,      # API 根路由返回的轻量服务状态
    HealthResponse,    # 健康检查响应
    ErrorResponse,     # 统一错误响应结构
    SuccessResponse,   # 通用成功响应
)

# 从 analysis.py 导入分析相关模型
from api.v1.schemas.analysis import (
    AnalyzeRequest,           # 触发股票分析的请求体
    AnalysisResultResponse,   # 同步接口或任务轮询返回的分析结果
    TaskAccepted,             # 新受理的异步分析任务响应
    BatchTaskAcceptedResponse,  # 批量分析任务提交汇总响应
    TaskStatus,               # 分析任务与大盘复盘任务的轮询响应
)

# 从 history.py 导入历史记录相关模型
from api.v1.schemas.history import (
    HistoryItem,              # 单条历史分析记录
    HistoryListResponse,      # 历史记录列表分页响应
    DeleteHistoryRequest,     # 删除历史记录的请求体
    DeleteHistoryResponse,    # 删除历史记录的结果响应
    NewsIntelItem,            # 单条新闻情报条目
    NewsIntelResponse,        # 新闻情报列表响应
    AnalysisReport,           # 分析报告详情
    ReportMeta,               # 报告元数据
    ReportSummary,            # 报告摘要
    ReportStrategy,           # 报告策略信息
    ReportDetails,            # 报告详细信息
)

# 从 stocks.py 导入股票行情相关模型
from api.v1.schemas.stocks import (
    StockQuote,               # 单只股票行情报价
    StockHistoryResponse,     # 股票历史行情响应
    KLineData,                # K 线数据
)

# 从 stock_selection.py 导入选股相关模型
from api.v1.schemas.stock_selection import (
    StockSelectionCandidateItem,      # 被选中的单只股票候选
    StockSelectionDiagnosticsItem,    # 单次选股运行的过程诊断计数器
    StockSelectionRequest,            # 运行一次选股策略的请求载荷
    StockSelectionResponse,           # 运行选股后的完整结果响应
    StockSelectionStrategiesResponse,  # 当前已注册的选股策略清单响应
    StockSelectionStrategyItem,       # 单个选股策略的元数据
)

# 从 backtest.py 导入回测相关模型
from api.v1.schemas.backtest import (
    BacktestRunRequest,       # 触发或刷新回测计算的请求体
    BacktestRunResponse,      # 回测任务完成后的概要计数响应
    BacktestResultItem,       # 单条历史分析记录的回测结果
    BacktestResultsResponse,  # 回测结果记录的分页列表响应
    PerformanceMetrics,       # 按 scope / code / 评估窗口聚合的回测表现指标
)

# 从 system_config.py 导入系统配置相关模型
from api.v1.schemas.system_config import (
    SystemConfigFieldSchema,              # 系统配置字段 Schema
    SystemConfigCategorySchema,           # 系统配置分类 Schema
    SystemConfigSchemaResponse,           # 系统配置 Schema 响应
    SystemConfigItem,                     # 单个系统配置项
    SystemConfigResponse,                 # 系统配置查询响应
    ExportSystemConfigResponse,           # 导出系统配置响应
    SystemConfigUpdateItem,               # 系统配置更新项
    UpdateSystemConfigRequest,            # 更新系统配置请求
    UpdateSystemConfigResponse,           # 更新系统配置响应
    ValidateSystemConfigRequest,          # 验证系统配置请求
    ImportSystemConfigRequest,            # 导入系统配置请求
    ConfigValidationIssue,                # 配置验证问题
    ValidateSystemConfigResponse,         # 验证系统配置响应
    LLMCapabilityCheck,                   # LLM 能力检查
    LLMCapabilityCheckResult,             # LLM 能力检查结果
    TestLLMChannelRequest,                # 测试 LLM 通道请求
    TestLLMChannelResponse,               # 测试 LLM 通道响应
    SystemConfigValidationErrorResponse,  # 系统配置验证错误响应
    SystemConfigConflictResponse,         # 系统配置冲突响应
)

# 从 portfolio.py 导入组合相关模型
from api.v1.schemas.portfolio import (
    PortfolioAccountCreateRequest,            # 创建组合账户的请求载荷
    PortfolioAccountUpdateRequest,            # 组合账户的局部更新请求体
    PortfolioAccountItem,                     # 组合账户列表接口返回的账户元数据
    PortfolioAccountListResponse,             # 组合账户列表的响应体
    PortfolioTradeCreateRequest,              # 记录一笔买入/卖出交易事件的请求载荷
    PortfolioCashLedgerCreateRequest,         # 记录现金存取事件的请求载荷
    PortfolioCorporateActionCreateRequest,    # 记录分红或拆合股调整事件的请求载荷
    PortfolioEventCreatedResponse,            # 组合事件表写入成功后的通用响应
    PortfolioTradeListItem,                   # 交易历史列表中的一笔标准化交易记录
    PortfolioTradeListResponse,               # 交易历史的分页响应
    PortfolioCashLedgerListItem,              # 现金流水列表中的一条存取记录
    PortfolioCashLedgerListResponse,          # 现金流水的分页响应
    PortfolioCorporateActionListItem,         # 公司行为列表中的一条事件记录
    PortfolioCorporateActionListResponse,     # 公司行为的分页响应
    PortfolioPositionItem,                    # 单只持仓的当前估值快照
    PortfolioPositionAnalysisRequest,         # 为某只持仓提交分析任务的请求载荷
    PortfolioAccountSnapshot,                 # 账户级别的持仓、现金与盈亏快照
    PortfolioSnapshotResponse,                # 跨账户聚合的组合级总览快照
    PortfolioImportTradeItem,                 # 从券商对账单解析后的一条交易记录
    PortfolioImportParseResponse,             # 上传/导入券商对账单后的解析结果
    PortfolioImportCommitResponse,            # 导入解析结果落库后的提交结果
    PortfolioImportBrokerItem,                # 支持的券商解析器元数据
    PortfolioImportBrokerListResponse,        # 支持的券商解析器列表响应
    PortfolioFxRefreshResponse,               # 为组合估值刷新汇率的结果响应
    PortfolioRiskResponse,                    # 按风险维度分组的组合风险摘要响应
)

# 从 alerts.py 导入告警相关模型
from api.v1.schemas.alerts import (
    AlertDeleteResponse,              # 告警规则删除操作的概要响应
    AlertNotificationItem,            # 由告警触发产生的单次通知投递记录
    AlertNotificationListResponse,    # 告警通知投递记录的分页响应
    AlertRuleCreateRequest,           # 创建告警规则的请求体
    AlertRuleItem,                    # 返回给 API 客户端的告警规则完整表示
    AlertRuleListResponse,            # 告警规则列表的分页响应
    AlertRuleTestResponse,            # 对单条告警规则进行试跑（dry-run）的评估结果
    AlertRuleUpdateRequest,           # 对已有告警规则做局部更新的请求体
    AlertTriggerItem,                 # 单条历史告警触发事件
    AlertTriggerListResponse,         # 告警触发历史的分页响应
)

# 定义 __all__ 以控制模块的公开接口
__all__ = [
    # common: 基础响应模型
    "RootResponse",
    "HealthResponse",
    "ErrorResponse",
    "SuccessResponse",
    # analysis: 分析相关模型
    "AnalyzeRequest",
    "AnalysisResultResponse",
    "TaskAccepted",
    "BatchTaskAcceptedResponse",
    "TaskStatus",
    # history: 历史记录相关模型
    "HistoryItem",
    "HistoryListResponse",
    "DeleteHistoryRequest",
    "DeleteHistoryResponse",
    "NewsIntelItem",
    "NewsIntelResponse",
    "AnalysisReport",
    "ReportMeta",
    "ReportSummary",
    "ReportStrategy",
    "ReportDetails",
    # stocks: 股票行情相关模型
    "StockQuote",
    "StockHistoryResponse",
    "KLineData",
    # stock selection: 选股相关模型
    "StockSelectionCandidateItem",
    "StockSelectionDiagnosticsItem",
    "StockSelectionRequest",
    "StockSelectionResponse",
    "StockSelectionStrategiesResponse",
    "StockSelectionStrategyItem",
    # backtest: 回测相关模型
    "BacktestRunRequest",
    "BacktestRunResponse",
    "BacktestResultItem",
    "BacktestResultsResponse",
    "PerformanceMetrics",
    # system config: 系统配置相关模型
    "SystemConfigFieldSchema",
    "SystemConfigCategorySchema",
    "SystemConfigSchemaResponse",
    "SystemConfigItem",
    "SystemConfigResponse",
    "ExportSystemConfigResponse",
    "SystemConfigUpdateItem",
    "UpdateSystemConfigRequest",
    "UpdateSystemConfigResponse",
    "ValidateSystemConfigRequest",
    "ImportSystemConfigRequest",
    "ConfigValidationIssue",
    "ValidateSystemConfigResponse",
    "LLMCapabilityCheck",
    "LLMCapabilityCheckResult",
    "TestLLMChannelRequest",
    "TestLLMChannelResponse",
    "SystemConfigValidationErrorResponse",
    "SystemConfigConflictResponse",
    # portfolio: 组合相关模型
    "PortfolioAccountCreateRequest",
    "PortfolioAccountUpdateRequest",
    "PortfolioAccountItem",
    "PortfolioAccountListResponse",
    "PortfolioTradeCreateRequest",
    "PortfolioCashLedgerCreateRequest",
    "PortfolioCorporateActionCreateRequest",
    "PortfolioEventCreatedResponse",
    "PortfolioTradeListItem",
    "PortfolioTradeListResponse",
    "PortfolioCashLedgerListItem",
    "PortfolioCashLedgerListResponse",
    "PortfolioCorporateActionListItem",
    "PortfolioCorporateActionListResponse",
    "PortfolioPositionItem",
    "PortfolioPositionAnalysisRequest",
    "PortfolioAccountSnapshot",
    "PortfolioSnapshotResponse",
    "PortfolioImportTradeItem",
    "PortfolioImportParseResponse",
    "PortfolioImportCommitResponse",
    "PortfolioImportBrokerItem",
    "PortfolioImportBrokerListResponse",
    "PortfolioFxRefreshResponse",
    "PortfolioRiskResponse",
    # alerts: 告警相关模型
    "AlertDeleteResponse",
    "AlertNotificationItem",
    "AlertNotificationListResponse",
    "AlertRuleCreateRequest",
    "AlertRuleItem",
    "AlertRuleListResponse",
    "AlertRuleTestResponse",
    "AlertRuleUpdateRequest",
    "AlertTriggerItem",
    "AlertTriggerListResponse",
]
