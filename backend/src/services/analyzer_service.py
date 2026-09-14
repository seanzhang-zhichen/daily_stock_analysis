# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 分析服务层
===================================

职责：
1. 封装核心分析逻辑，支持多调用方（CLI、WebUI、Bot）
2. 提供清晰的API接口，不依赖于命令行参数
3. 支持依赖注入，便于测试和扩展
4. 统一管理分析流程和配置

该模块作为分析服务的高层封装，为不同调用方（命令行、Web界面、机器人）
提供统一的分析接口，隐藏底层实现细节。
"""

import uuid
from typing import List, Optional

# 导入分析结果数据模型，用于类型提示和结果处理
from src.analyzer import AnalysisResult
# 导入大盘复盘核心函数，用于生成市场整体分析报告
from src.core.market_review import run_market_review
# 导入股票分析流水线，协调各个分析步骤的执行
from src.core.pipeline import StockAnalysisPipeline
# 导入配置类和相关函数，用于获取系统配置
from src.config import Config, get_config
# 导入报告类型枚举，定义支持的报告格式
from src.enums import ReportType
# 导入通知服务，用于分析完成后的消息推送
from src.notification import NotificationService


def analyze_stock(
    stock_code: str,
    config: Config = None,
    full_report: bool = False,
    notifier: Optional[NotificationService] = None,
) -> Optional[AnalysisResult]:
    """
    分析单只股票

    对指定股票执行完整的分析流程，包括数据获取、技术指标计算、
    基本面分析、情绪分析等，并生成结构化的分析报告。

    Args:
        stock_code: 股票代码，支持带市场前缀的格式（如 "SH.600000"）
        config: 配置对象（可选），用于自定义分析参数，
                如未提供则使用全局单例配置
        full_report: 是否生成完整报告，True 表示生成包含所有维度的详细报告，
                     False 表示生成简要报告
        notifier: 通知服务实例（可选），分析完成后用于发送通知消息

    Returns:
        Optional[AnalysisResult]: 分析结果对象，包含分析结论、技术指标、
                                   基本面数据等；如果分析失败则返回 None
    """
    # 如果未提供配置，则使用全局单例配置
    # 全局配置包含模型参数、数据源配置、缓存策略等
    if config is None:
        config = get_config()

    # 创建分析流水线实例，这是分析执行的核心协调器
    # query_id 使用 UUID 唯一标识本次分析请求
    pipeline = StockAnalysisPipeline(
        config=config,
        query_id=uuid.uuid4().hex,
        query_source="cli"  # 标记查询来源为命令行
    )

    # 如果调用方提供了通知服务，则将其注入到流水线中
    # 这样分析完成后可以自动发送通知
    if notifier:
        pipeline.notifier = notifier

    # 根据 full_report 参数确定报告类型
    # FULL: 完整报告，包含所有分析维度
    # SIMPLE: 简要报告，仅包含核心结论
    report_type = ReportType.FULL if full_report else ReportType.SIMPLE

    # 运行单只股票分析，调用流水线的核心分析方法
    result = pipeline.process_single_stock(
        code=stock_code,
        skip_analysis=False,  # 不跳过分析，执行完整的分析流程
        single_stock_notify=notifier is not None,  # 如果有通知服务则发送通知
        report_type=report_type,
    )

    return result


def analyze_stocks(
    stock_codes: List[str],
    config: Config = None,
    full_report: bool = False,
    notifier: Optional[NotificationService] = None,
) -> List[AnalysisResult]:
    """
    分析多只股票

    对股票代码列表中的每只股票依次执行分析，
    返回所有成功分析的结果列表。

    Args:
        stock_codes: 股票代码列表，每个元素为单个股票代码字符串
        config: 配置对象（可选），如未提供则使用全局单例配置
        full_report: 是否生成完整报告，True 表示生成详细报告
        notifier: 通知服务实例（可选），用于分析完成后的通知

    Returns:
        List[AnalysisResult]: 分析结果列表，仅包含成功分析的股票结果，
                              失败的股票会被跳过
    """
    # 如果未提供配置，则使用全局单例配置
    if config is None:
        config = get_config()

    # 存储所有成功分析的结果
    results = []
    # 遍历股票代码列表，逐只进行分析
    for stock_code in stock_codes:
        # 调用单股票分析函数
        result = analyze_stock(stock_code, config, full_report, notifier)
        # 仅将成功的结果加入列表
        if result:
            results.append(result)

    return results


def perform_market_review(
    config: Config = None,
    notifier: Optional[NotificationService] = None,
) -> Optional[str]:
    """
    执行大盘复盘

    对整体市场进行复盘分析，生成市场综述报告，
    包括市场热点、板块表现、资金流向等宏观分析。

    Args:
        config: 配置对象（可选），如未提供则使用全局单例配置
        notifier: 通知服务实例（可选），用于复盘完成后的通知推送

    Returns:
        Optional[str]: 复盘报告内容字符串，如果复盘失败则返回 None
    """
    # 如果未提供配置，则使用全局单例配置
    if config is None:
        config = get_config()

    # 创建分析流水线以获取 analyzer 和 search_service 实例
    # 这些实例在大盘复盘中用于执行具体的分析任务
    pipeline = StockAnalysisPipeline(
        config=config,
        query_id=uuid.uuid4().hex,
        query_source="cli",
    )

    # 优先使用调用方提供的通知服务，如未提供则使用流水线默认的通知服务
    review_notifier = notifier or pipeline.notifier

    # 调用大盘复盘核心函数，传入通知服务、分析器和搜索服务
    # 这些组件协同工作，完成市场复盘分析
    return run_market_review(
        notifier=review_notifier,
        analyzer=pipeline.analyzer,
        search_service=pipeline.search_service,
    )

