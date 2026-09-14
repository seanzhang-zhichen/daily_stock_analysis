# -*- coding: utf-8 -*-
"""分析服务层。

职责：
1. 封装股票分析逻辑
2. 调用 analyzer 与 pipeline 执行分析
3. 把分析结果落库

该模块作为业务逻辑层，负责协调数据获取、分析执行和结果格式化，
为上层 API 提供统一的股票分析接口。
"""

import logging
import uuid
from typing import Optional, Dict, Any, Callable, List

# 导入分析结果数据访问对象，用于将分析结果持久化到数据库
from src.repositories.analysis_repo import AnalysisRepository
# 导入报告语言本地化工具，支持多语言报告生成
from src.report_language import (
    get_sentiment_label,           # 根据情绪分数获取对应的情绪标签文本
    get_localized_stock_name,       # 获取本地化的股票名称
    localize_operation_advice,      # 本地化操作建议文本
    localize_trend_prediction,      # 本地化趋势预测文本
    normalize_report_language,      # 规范化报告语言设置
)
# 导入诊断工具，用于追踪分析执行过程中的上下文和状态
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,  # 激活诊断上下文，记录当前执行环境
    current_diagnostic_snapshot,      # 获取当前诊断快照
    get_current_diagnostic_context,   # 获取当前诊断上下文
    reset_run_diagnostic_context,     # 重置诊断上下文
)

# 创建日志记录器实例，用于记录分析服务的运行日志
logger = logging.getLogger(__name__)


class AnalysisService:
    """
    分析服务类

    封装股票分析相关的业务逻辑，提供统一的股票分析接口。
    负责协调分析流水线、处理分析结果、构建标准化响应。

    主要功能：
    - 执行单只股票的分析
    - 管理分析过程中的诊断上下文
    - 构建结构化的分析响应数据

    Attributes:
        repo (AnalysisRepository): 分析结果数据访问对象
        last_error (Optional[str]): 最后一次错误信息，用于错误追踪
    """

    def __init__(self):
        """
        初始化分析服务实例

        创建分析结果仓库实例，初始化错误状态。
        """
        self.repo = AnalysisRepository()
        self.last_error: Optional[str] = None
    
    def analyze_stock(
        self,
        stock_code: str,
        report_type: str = "detailed",
        force_refresh: bool = False,
        query_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        send_notification: bool = True,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        skills: Optional[List[str]] = None,
        user_id: Optional[int] = None,
        query_source: str = "api",
        analysis_phase: str = "auto",
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        执行股票分析

        这是分析服务的核心方法，负责协调整个股票分析流程。
        包括创建分析流水线、执行分析、处理结果和构建响应。

        分析流程：
        1. 生成或复用查询标识
        2. 激活诊断上下文（如未激活）
        3. 创建分析流水线实例
        4. 执行分析并获取结果
        5. 构建标准化响应数据

        Args:
            stock_code: 股票代码，支持带市场前缀的格式（如 "SH.600000" 或 "600000.SH"）
            report_type: 报告类型，可选值包括：
                - "simple": 简要报告，仅包含核心分析结论
                - "detailed": 详细报告，包含完整的技术面和基本面分析
                - "full": 完整报告，包含所有可用分析维度
                - "brief": 极简报告，仅关键数据
            force_refresh: 是否强制刷新，忽略缓存直接重新分析
            query_id: 查询 ID（可选），用于追踪单次查询请求，
                     如未提供则自动生成 UUID
            trace_id: 追踪 ID（可选），用于分布式追踪，
                     如未提供则使用 query_id 作为 trace_id
            send_notification: 是否发送通知，API 触发时默认为 True，
                              后台任务可能设为 False
            progress_callback: 进度回调函数，接收进度百分比和状态消息，
                              用于向客户端实时反馈分析进度
            skills: 分析技能列表（可选），指定使用哪些分析能力
            user_id: 用户 ID（可选），To C 模式下的归属用户 ID；
                    单租户/CLI/Bot 路径保持 None
            query_source: 查询来源，默认为 "api"，
                         可选值包括 "api", "cli", "bot", "scheduler" 等
            analysis_phase: 分析阶段，默认为 "auto"，
                           用于控制分析粒度和深度
            portfolio_context: 投资组合上下文（可选），
                              包含持仓信息、成本价等，用于个性化分析

        Returns:
            Optional[Dict[str, Any]]: 分析结果字典，包含以下字段：
                - query_id: 查询 ID
                - trace_id: 追踪 ID
                - stock_code: 股票代码
                - stock_name: 股票名称
                - report: 结构化分析报告
                - diagnostics: 诊断信息快照
            如果分析失败，返回 None

        Raises:
            本方法捕获所有异常，不会向外抛出，
            错误信息会记录到日志和 self.last_error
        """
        try:
            self.last_error = None
            # 导入分析相关模块
            # get_config 用于获取系统配置，包含模型参数、数据源配置等
            from src.config import get_config
            # StockAnalysisPipeline 是核心分析流水线，负责协调各个分析步骤
            from src.core.pipeline import StockAnalysisPipeline
            # ReportType 枚举定义了支持的报告类型
            from src.enums import ReportType

            # 生成 query_id：如果调用方未提供，则自动生成 UUID
            # query_id 用于唯一标识一次分析请求，贯穿整个分析生命周期
            if query_id is None:
                query_id = uuid.uuid4().hex
            # effective_trace_id 用于分布式追踪，优先使用传入的 trace_id，
            # 如未提供则复用 query_id
            effective_trace_id = trace_id or query_id
            # diagnostic_token 用于管理诊断上下文的激活状态
            diagnostic_token = None
            # 如果当前没有活动的诊断上下文，则激活一个新的诊断上下文
            # 诊断上下文记录了当前执行环境的关键信息，便于问题排查
            if get_current_diagnostic_context() is None:
                diagnostic_token = activate_run_diagnostic_context(
                    trace_id=effective_trace_id,
                    query_id=query_id,
                    stock_code=stock_code,
                    trigger_source=query_source or "api",
                )

            # 获取系统配置，包含模型参数、数据源配置、缓存策略等
            config = get_config()

            # 创建分析流水线实例，这是分析执行的核心协调器
            # 流水线负责按顺序执行数据获取、技术指标计算、基本面分析、
            # 情绪分析、报告生成等步骤
            pipeline = StockAnalysisPipeline(
                config=config,
                query_id=query_id,
                trace_id=effective_trace_id,
                query_source=query_source or "api",
                progress_callback=progress_callback,
                analysis_skills=skills,
                analysis_phase=analysis_phase,
                portfolio_context=portfolio_context,
                user_id=user_id,
            )

            # 确定报告类型：将字符串类型的报告类型转换为 ReportType 枚举
            # 支持从字符串（如 "simple", "detailed"）到枚举值的转换
            rt = ReportType.from_str(report_type)

            # 执行分析：调用流水线的 process_single_stock 方法进行单股票分析
            # 该方法会协调数据获取、分析计算、结果生成等完整流程
            result = pipeline.process_single_stock(
                code=stock_code,
                skip_analysis=False,
                single_stock_notify=send_notification,
                report_type=rt,
            )

            # 检查分析结果是否为空
            if result is None:
                logger.warning(f"分析股票 {stock_code} 返回空结果")
                self.last_error = self.last_error or f"分析股票 {stock_code} 返回空结果"
                return None

            # 检查分析是否成功完成
            # result.success 表示分析流程是否正常完成
            if not getattr(result, "success", True):
                self.last_error = getattr(result, "error_message", None) or f"分析股票 {stock_code} 失败"
                logger.warning(f"分析股票 {stock_code} 未成功完成: {self.last_error}")
                return None

            # 构建标准化响应：将分析结果转换为结构化的响应字典
            # 包含元数据、摘要、策略建议、详细分析等模块
            return self._build_analysis_response(
                result,
                query_id,
                trace_id=effective_trace_id,
                report_type=rt.value,
            )

        except Exception as e:
            # 捕获所有异常，记录错误日志，更新 last_error 状态
            # 不向外抛出异常，保证接口的稳定性
            self.last_error = str(e)
            logger.error(f"分析股票 {stock_code} 失败: {e}", exc_info=True)
            return None
        finally:
            # 无论成功或失败，都重置诊断上下文
            # 避免诊断上下文泄漏，影响后续请求
            reset_run_diagnostic_context(locals().get("diagnostic_token"))
    
    def _build_analysis_response(
        self,
        result: Any,
        query_id: str,
        trace_id: Optional[str] = None,
        report_type: str = "detailed",
    ) -> Dict[str, Any]:
        """
        构建分析响应

        将分析结果对象转换为结构化的响应字典，
        包含元数据、摘要、策略建议和详细分析等模块。

        该方法负责：
        1. 提取狙击点位（理想买入、二次买入、止损、止盈）
        2. 计算情绪标签和本地化股票名称
        3. 构建结构化的报告数据

        Args:
            result: AnalysisResult 对象，包含分析结果的原始数据
            query_id: 查询 ID，用于追踪请求
            trace_id: 追踪 ID（可选），用于分布式追踪
            report_type: 归一化后的报告类型，如 "simple", "detailed", "full", "brief"

        Returns:
            Dict[str, Any]: 格式化的响应字典，包含以下结构：
                - query_id: 查询 ID
                - trace_id: 追踪 ID
                - stock_code: 股票代码
                - stock_name: 本地化后的股票名称
                - report: 结构化报告，包含 meta、summary、strategy、details 四个子模块
                - diagnostics: 诊断信息快照
        """
        # 获取狙击点位：包含理想买入价、二次买入价、止损价、止盈价
        # 狙击点位是基于技术分析计算出的关键价格区间
        sniper_points = {}
        if hasattr(result, 'get_sniper_points'):
            sniper_points = result.get_sniper_points() or {}

        # 计算报告语言和情绪标签
        # report_language 控制报告输出语言（"zh" 表示中文）
        report_language = normalize_report_language(getattr(result, "report_language", "zh"))
        # 根据情绪分数（-1 到 1）获取对应的情绪标签文本（如 "极度乐观"、"谨慎" 等）
        sentiment_label = get_sentiment_label(result.sentiment_score, report_language)
        # 获取本地化的股票名称，优先使用分析结果中的名称，
        # 如未提供则根据股票代码和语言设置生成
        stock_name = get_localized_stock_name(getattr(result, "name", None), result.code, report_language)

        # 构建报告结构：包含四个主要模块
        # - meta: 元数据，包含查询信息、股票基本信息等
        # - summary: 摘要，包含分析结论、操作建议、趋势预测、情绪评分
        # - strategy: 策略，包含狙击点位（理想买入、二次买入、止损、止盈）
        # - details: 详情，包含新闻摘要、技术分析、基本面分析、风险提示、公司简介
        report = {
            "meta": {
                "query_id": query_id,
                "trace_id": trace_id or query_id,
                "stock_code": result.code,
                "stock_name": stock_name,
                "report_type": report_type,
                "report_language": report_language,
                "current_price": result.current_price,      # 当前价格（最新收盘价）
                "change_pct": result.change_pct,            # 涨跌幅百分比
                "model_used": getattr(result, "model_used", None),  # 使用的 AI 模型
            },
            "summary": {
                "analysis_summary": result.analysis_summary,        # 分析摘要文本
                "operation_advice": localize_operation_advice(result.operation_advice, report_language),  # 操作建议
                "trend_prediction": localize_trend_prediction(result.trend_prediction, report_language),    # 趋势预测
                "sentiment_score": result.sentiment_score,          # 情绪分数（-1 到 1）
                "sentiment_label": sentiment_label,                 # 情绪标签文本
            },
            "strategy": {
                "ideal_buy": sniper_points.get("ideal_buy"),        # 理想买入价位
                "secondary_buy": sniper_points.get("secondary_buy"), # 二次买入价位（更保守的买入点）
                "stop_loss": sniper_points.get("stop_loss"),       # 止损价位
                "take_profit": sniper_points.get("take_profit"),   # 止盈价位
            },
            "details": {
                "news_summary": result.news_summary,                # 新闻摘要
                "technical_analysis": result.technical_analysis,    # 技术分析详情
                "fundamental_analysis": result.fundamental_analysis, # 基本面分析详情
                "risk_warning": result.risk_warning,                # 风险提示
                "stock_profile": getattr(result, "stock_profile", None),  # 公司简介
            }
        }

        # 返回完整的响应字典，包含查询信息、股票信息和结构化报告
        return {
            "query_id": query_id,
            "trace_id": trace_id or query_id,
            "stock_code": result.code,
            "stock_name": stock_name,
            "report": report,
            "diagnostics": current_diagnostic_snapshot(),  # 包含诊断信息，便于问题排查
        }
