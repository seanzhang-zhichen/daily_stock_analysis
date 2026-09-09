# -*- coding: utf-8 -*-
"""核心股票分析流水线（pipeline）。

``StockAnalysisPipeline`` 负责协调行情数据、技术分析、可选的搜索/Agent 研究、持久化、
报告生成与通知。本文件刻意让副作用边界清晰可见：抓取/存储/分析/通知各自调用独立服务，
而本模块只负责编排与兜底（fallback）策略。
"""

import logging
import re
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple, Callable

import pandas as pd

from src.config import FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT, get_config, Config
from src.storage import get_db
from data_provider import DataFetcherManager
from data_provider.base import normalize_stock_code
from data_provider.realtime_types import ChipDistribution
from src.analyzer import (
    GeminiAnalyzer,
    AnalysisResult,
    fill_chip_structure_if_needed,
    fill_price_position_if_needed,
    stabilize_decision_with_structure,
)
from src.daily_market_context_guardrail import apply_daily_market_context_guardrail
from src.data.stock_mapping import STOCK_NAME_MAP
from src.notification import NotificationService, NotificationChannel
from src.report_language import (
    get_unknown_text,
    infer_decision_type_from_advice,
    localize_confidence_level,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.search_service import SearchService
from src.services.daily_market_context import (
    DailyMarketContext,
    DailyMarketContextService,
    format_daily_market_context_prompt_section,
)
from src.services.market_structure_service import MarketStructureService
from src.services.empty_news import news_evidence_present
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    current_diagnostic_snapshot,
    get_current_diagnostic_context,
    record_history_run,
    reset_run_diagnostic_context,
)
from src.agent.news_evidence import (
    activate_news_evidence_scope,
    get_current_news_evidence,
    reset_news_evidence_scope,
)
from src.services.social_sentiment_service import SocialSentimentService
from src.enums import ReportType
from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult
from src.core.trading_calendar import (
    build_market_phase_context,
    get_effective_trading_date,
    get_market_for_stock,
    get_market_now,
    is_market_open,
)
from src.market_phase_summary import MARKET_PHASE_SUMMARY_KEY, render_market_phase_summary
from data_provider.us_index_mapping import is_us_stock_code
from bot.models import BotMessage


logger = logging.getLogger(__name__)

# 防御性 guard：当实例绕过 __init__（如测试中 __new__）构造时，
# double-check 初始化 _single_stock_notify_lock 仍然线程安全。
_SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD = threading.Lock()


def _a_share_intelligence_scope_values(code: str) -> List[str]:
    """返回 6 位 A 股代码对应的已持久化新闻作用域别名集合。"""
    raw = str(code or "").strip()
    digits = raw[-6:] if len(raw) >= 6 and raw[-6:].isdigit() else ""
    if not digits:
        return []
    exchange = "SH" if digits.startswith(("5", "6", "9")) else "SZ"
    values = (
        digits, digits.upper(), digits.lower(), f"{exchange}{digits}",
        f"{exchange.lower()}{digits}", f"{digits}.{exchange}", f"{digits}.{exchange.lower()}",
    )
    return list(dict.fromkeys(values))


class StockAnalysisPipeline:
    """股票分析主流程调度器。

    该流水线被 CLI、定时任务、API 后台任务与 Bot 入口共用。``query_id``、
    ``source_message``、``user_id`` 等可选构造参数用于在这些入口间传递请求归属信息，
    而不改动核心分析流程。

    职责：
    1. 统筹管理整个分析流程
    2. 协调数据获取、存储、搜索、分析、通知等模块
    3. 实现并发控制与异常处理
    """
    _STOCK_PROFILE_META_LINE_PATTERN = re.compile(
        r"^\s*(?:如果你愿意|如需我|我可以|我还能|下一步我可以|下一步可以|是否需要我|Would you like|I can|If you want|Next[, ]+I can)",
        re.IGNORECASE,
    )
    
    def __init__(
        self,
        config: Optional[Config] = None,
        max_workers: Optional[int] = None,
        source_message: Optional[BotMessage] = None,
        query_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        query_source: Optional[str] = None,
        save_context_snapshot: Optional[bool] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        analysis_skills: Optional[List[str]] = None,
        user_id: Optional[int] = None,
        analysis_phase: str = "auto",
        portfolio_context: Optional[Dict[str, Any]] = None,
        daily_market_context_enabled: Optional[bool] = None,
        daily_market_context_allow_generate: bool = True,
    ):
        """
        初始化调度器
        
        Args:
            config: 配置对象（可选，默认使用全局配置）
            max_workers: 最大并发线程数（可选，默认从配置读取）
            user_id: To C 模式下的归属用户 ID; 单租户/CLI/Bot 路径保持 ``None``，
                此时 ``save_analysis_history`` 入库 ``user_id=NULL``，与旧数据兼容。
        """
        self.config = config or get_config()
        self.max_workers = max_workers or self.config.max_workers
        self.source_message = source_message
        self.query_id = query_id
        self.trace_id = trace_id or query_id
        self.query_source = self._resolve_query_source(query_source)
        self.save_context_snapshot = (
            self.config.save_context_snapshot if save_context_snapshot is None else save_context_snapshot
        )
        self.progress_callback = progress_callback
        self.analysis_skills = list(analysis_skills) if analysis_skills is not None else None
        self.analysis_phase = analysis_phase or "auto"
        self.portfolio_context = (
            dict(portfolio_context) if isinstance(portfolio_context, dict) else None
        )
        self.user_id = user_id
        self.daily_market_context_enabled = (
            bool(getattr(self.config, "daily_market_context_enabled", True))
            if daily_market_context_enabled is None
            else bool(daily_market_context_enabled)
        )
        self.daily_market_context_allow_generate = daily_market_context_allow_generate
        self._daily_market_context_service_lock = threading.Lock()
        
        # 初始化各模块
        self.db = get_db()
        self.fetcher_manager = DataFetcherManager()
        self.market_structure_service = MarketStructureService(
            fetcher_manager=self.fetcher_manager,
        )
        # 不再单独创建 akshare_fetcher，统一使用 fetcher_manager 获取增强数据
        self.trend_analyzer = StockTrendAnalyzer()  # 技术分析器
        self.analyzer = GeminiAnalyzer(config=self.config, skills=self.analysis_skills, user_id=user_id)
        self.notifier = NotificationService(source_message=source_message)
        self._single_stock_notify_lock = threading.Lock()
        
        # 初始化搜索服务（可选，初始化失败不应阻断主分析流程）
        try:
            self.search_service = SearchService(
                bocha_keys=self.config.bocha_api_keys,
                tavily_keys=self.config.tavily_api_keys,
                anspire_keys=self.config.anspire_api_keys,
                brave_keys=self.config.brave_api_keys,
                serpapi_keys=self.config.serpapi_keys,
                minimax_keys=self.config.minimax_api_keys,
                searxng_base_urls=self.config.searxng_base_urls,
                searxng_public_instances_enabled=self.config.searxng_public_instances_enabled,
                news_max_age_days=self.config.news_max_age_days,
                news_strategy_profile=getattr(self.config, "news_strategy_profile", "short"),
            )
        except Exception as exc:
            logger.warning("搜索服务初始化失败，将以无搜索模式运行: %s", exc, exc_info=True)
            self.search_service = None
        
        logger.info(f"调度器初始化完成，最大并发数: {self.max_workers}")
        logger.info("已启用技术分析引擎（均线/趋势/量价指标）")
        # 打印实时行情/筹码配置状态
        if self.config.enable_realtime_quote:
            logger.info(f"实时行情已启用 (优先级: {self.config.realtime_source_priority})")
        else:
            logger.info("实时行情已禁用，将使用历史收盘价")
        if self.config.enable_chip_distribution:
            logger.info("筹码分布分析已启用")
        else:
            logger.info("筹码分布分析已禁用")
        if self.search_service is None:
            logger.warning("搜索服务未启用（初始化失败或依赖缺失）")
        elif self.search_service.is_available:
            logger.info("搜索服务已启用")
        else:
            logger.warning("搜索服务未启用（未配置搜索能力）")

        # 初始化社交舆情服务（仅美股，可选）
        try:
            self.social_sentiment_service = SocialSentimentService(
                api_key=self.config.social_sentiment_api_key,
                api_url=self.config.social_sentiment_api_url,
            )
            if self.social_sentiment_service.is_available:
                logger.info("Social sentiment service enabled (Reddit/X/Polymarket, US stocks only)")
        except Exception as exc:
            logger.warning(
                "社交舆情服务初始化失败，将跳过舆情分析: %s",
                exc,
                exc_info=True,
            )
            self.social_sentiment_service = None

    def _emit_progress(self, progress: int, message: str) -> None:
        """尽力将流水线各阶段进度桥接到任务的 SSE 进度推送（best-effort）。"""
        callback = getattr(self, "progress_callback", None)
        if callback is None:
            return
        try:
            callback(progress, message)
        except Exception as exc:
            query_id = getattr(self, "query_id", None)
            logger.warning(
                "[pipeline] progress callback failed: %s (progress=%s, message=%r, query_id=%s)",
                exc,
                progress,
                message,
                query_id,
                extra={
                    "progress": progress,
                    "progress_message": message,
                    "query_id": query_id,
                },
            )

    def fetch_and_save_stock_data(
        self, 
        code: str,
        force_refresh: bool = False,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        获取并保存单只股票数据
        
        断点续传逻辑：
        1. 检查数据库是否已有最新可复用交易日数据
        2. 如果有且不强制刷新，则跳过网络请求
        3. 否则从数据源获取并保存
        
        Args:
            code: 股票代码
            force_refresh: 是否强制刷新（忽略本地缓存）
            current_time: 本轮运行冻结的参考时间，用于统一断点续传目标交易日判断
            
        Returns:
            Tuple[是否成功, 错误信息]
        """
        stock_name = code
        try:
            # 首先获取股票名称
            stock_name = self.fetcher_manager.get_stock_name(code, allow_realtime=False)

            target_date = self._resolve_resume_target_date(
                code, current_time=current_time
            )

            # 断点续传检查：如果最新可复用交易日的数据已存在，则跳过
            if not force_refresh and self.db.has_today_data(code, target_date):
                logger.info(
                    f"{stock_name}({code}) {target_date} 数据已存在，跳过获取（断点续传）"
                )
                return True, None

            # 从数据源获取数据
            logger.info(f"{stock_name}({code}) 开始从数据源获取数据...")
            df, source_name = self.fetcher_manager.get_daily_data(code, days=30)

            if df is None or df.empty:
                return False, "获取数据为空"

            # 保存到数据库
            saved_count = self.db.save_daily_data(df, code, source_name)
            logger.info(f"{stock_name}({code}) 数据保存成功（来源: {source_name}，新增 {saved_count} 条）")

            return True, None

        except Exception as e:
            error_msg = f"获取/保存数据失败: {str(e)}"
            logger.error(f"{stock_name}({code}) {error_msg}")
            return False, error_msg
    
    def analyze_stock(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        current_time: Optional[datetime] = None,
    ) -> Optional[AnalysisResult]:
        """
        分析单只股票（增强版：含量比、换手率、筹码分析、多维度情报）
        
        流程：
        1. 获取实时行情（量比、换手率）- 通过 DataFetcherManager 自动故障切换
        2. 获取筹码分布 - 通过 DataFetcherManager 带熔断保护
        3. 进行趋势分析（基于交易理念）
        4. 多维度情报搜索（最新消息+风险排查+业绩预期）
        5. 从数据库获取分析上下文
        6. 调用 AI 进行综合分析
        
        Args:
            query_id: 查询链路关联 id
            code: 股票代码
            report_type: 报告类型
            
        Returns:
            AnalysisResult 或 None（如果分析失败）
        """
        stock_name = code
        try:
            market = get_market_for_stock(normalize_stock_code(code))
            market_phase_summary = None
            if market == "cn":
                market_phase_summary = render_market_phase_summary(
                    build_market_phase_context(
                        market=market,
                        current_time=current_time,
                        trigger_source=getattr(self, "query_source", "system"),
                        analysis_phase=getattr(self, "analysis_phase", "auto"),
                    ).to_dict()
                )
            daily_market_context = self._load_daily_market_context(market)
            self._emit_progress(18, f"{code}：正在获取行情与筹码数据")
            # 获取股票名称（先走轻量名称路径，后续若 realtime_quote 有 name 再覆盖）
            stock_name = self.fetcher_manager.get_stock_name(code, allow_realtime=False)

            # Step 1: 获取实时行情（量比、换手率等）- 使用统一入口，自动故障切换
            realtime_quote = None
            try:
                if self.config.enable_realtime_quote:
                    realtime_quote = self.fetcher_manager.get_realtime_quote(code, log_final_failure=False)
                    if realtime_quote:
                        # 使用实时行情返回的真实股票名称
                        if realtime_quote.name:
                            stock_name = realtime_quote.name
                        # 兼容不同数据源的字段（有些数据源可能没有 volume_ratio）
                        volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
                        turnover_rate = getattr(realtime_quote, 'turnover_rate', None)
                        logger.info(f"{stock_name}({code}) 实时行情: 价格={realtime_quote.price}, "
                                  f"量比={volume_ratio}, 换手率={turnover_rate}% "
                                  f"(来源: {realtime_quote.source.value if hasattr(realtime_quote, 'source') else 'unknown'})")
                    else:
                        logger.warning(f"{stock_name}({code}) 所有实时行情数据源均不可用，已降级为历史收盘价继续分析")
                else:
                    logger.info(f"{stock_name}({code}) 实时行情已禁用，使用历史收盘价继续分析")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 实时行情链路异常，已降级为历史收盘价继续分析: {e}")

            # 如果还是没有名称，使用代码作为名称
            if not stock_name:
                stock_name = f'股票{code}'

            # Step 2: 获取筹码分布 - 使用统一入口，带熔断保护
            chip_data = None
            try:
                chip_data = self.fetcher_manager.get_chip_distribution(code)
                if chip_data:
                    logger.info(f"{stock_name}({code}) 筹码分布: 获利比例={chip_data.profit_ratio:.1%}, "
                              f"90%集中度={chip_data.concentration_90:.2%}")
                else:
                    logger.debug(f"{stock_name}({code}) 筹码分布获取失败或已禁用")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 获取筹码分布失败: {e}")

            # 只有当 Agent 模式被显式开启、或配置了具体 Agent 技能时，才走 Agent 分析链路。
            # 注意：这里刻意用 config.agent_mode（显式 opt-in）而非 config.is_agent_available()，
            # 避免只为传统分析路径配了 API Key 的用户被静默切到更慢、更贵的 Agent 模式。
            use_agent = getattr(self.config, 'agent_mode', False)
            if not use_agent:
                if self.analysis_skills:
                    use_agent = True
                    logger.info(f"{stock_name}({code}) Auto-enabled agent mode due to request skills: {self.analysis_skills}")
            if not use_agent:
                # 配置了具体技能（如带策略的定时任务）时自动启用 Agent 模式；['all'] 视为未指定
                configured_skills = getattr(self.config, 'agent_skills', [])
                if configured_skills and configured_skills != ['all']:
                    use_agent = True
                    logger.info(f"{stock_name}({code}) Auto-enabled agent mode due to configured skills: {configured_skills}")

            self._emit_progress(32, f"{stock_name}：正在聚合基本面与趋势数据")

            # Step 2.5: 基本面能力聚合（统一入口，异常降级）
            # - 失败时返回 partial/failed，不影响既有技术面/新闻链路
            # - 关闭开关时仍返回 not_supported 结构
            fundamental_context = None
            try:
                fundamental_context = self.fetcher_manager.get_fundamental_context(
                    code,
                    budget_seconds=getattr(
                        self.config,
                        'fundamental_stage_timeout_seconds',
                        FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT,
                    ),
                )
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 基本面聚合失败: {e}")
                fundamental_context = self.fetcher_manager.build_failed_fundamental_context(code, str(e))

            fundamental_context = self._attach_belong_boards_to_fundamental_context(
                code,
                fundamental_context,
            )
            market_structure_context = self._build_market_structure_context(
                code=code,
                stock_name=stock_name,
                market=market,
                fundamental_context=fundamental_context,
                daily_market_context=daily_market_context,
                market_phase_summary=market_phase_summary,
            )

            # P0：该快照只写不读（write-only），失败必须放行，主流程不得依赖这张表
            try:
                self.db.save_fundamental_snapshot(
                    query_id=query_id,
                    code=code,
                    payload=fundamental_context,
                    source_chain=fundamental_context.get("source_chain", []),
                    coverage=fundamental_context.get("coverage", {}),
                )
            except Exception as e:
                logger.debug(f"{stock_name}({code}) 基本面快照写入失败: {e}")

            # Step 3: 趋势分析（基于交易理念）— 在 Agent 分支之前执行，供两条路径共用
            trend_result: Optional[TrendAnalysisResult] = None
            try:
                from src.services.history_loader import get_frozen_target_date
                _mkt = get_market_for_stock(normalize_stock_code(code))
                frozen = get_frozen_target_date()
                end_date = frozen if frozen else get_market_now(_mkt).date()
                start_date = end_date - timedelta(days=89)  # 89 个自然日约合 60 个交易日，满足 MA60 样本量
                historical_bars = self.db.get_data_range(code, start_date, end_date)
                if historical_bars:
                    df = pd.DataFrame([bar.to_dict() for bar in historical_bars])
                    # Issue #234：盘中用实时行情补齐当日 Bar，否则 MA 会停留在昨收口径
                    if self.config.enable_realtime_quote and realtime_quote:
                        df = self._augment_historical_with_realtime(df, realtime_quote, code)
                    trend_result = self.trend_analyzer.analyze(df, code)
                    logger.info(f"{stock_name}({code}) 趋势分析: {trend_result.trend_status.value}, "
                              f"买入信号={trend_result.buy_signal.value}, 评分={trend_result.signal_score}")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 趋势分析失败: {e}", exc_info=True)

            if use_agent:
                logger.info(f"{stock_name}({code}) 启用 Agent 模式进行分析")
                self._emit_progress(58, f"{stock_name}：正在切换 Agent 分析链路")
                return self._analyze_with_agent(
                    code,
                    report_type,
                    query_id,
                    stock_name,
                    realtime_quote,
                    chip_data,
                    fundamental_context,
                    trend_result,
                    daily_market_context=daily_market_context,
                    market_structure_context=market_structure_context,
                    market_phase_summary=market_phase_summary,
                )

            # Step 4: 多维度情报搜索（最新消息+风险排查+业绩预期）
            news_context = None
            news_result_count: Optional[int] = None
            self._emit_progress(46, f"{stock_name}：正在检索新闻与舆情")
            if self.search_service is not None and self.search_service.is_available:
                news_result_count = 0
                logger.info(f"{stock_name}({code}) 开始多维度情报搜索...")

                # 使用多维度搜索（最多5次搜索）
                intel_results = self.search_service.search_comprehensive_intel(
                    stock_code=code,
                    stock_name=stock_name,
                    max_searches=5
                )

                # 格式化情报报告
                if intel_results:
                    news_context = self.search_service.format_intel_report(intel_results, stock_name)
                    total_results = sum(
                        len(r.results) for r in intel_results.values() if r.success
                    )
                    news_result_count = total_results
                    logger.info(f"{stock_name}({code}) 情报搜索完成: 共 {total_results} 条结果")
                    logger.debug(f"{stock_name}({code}) 情报搜索结果:\n{news_context}")

                    # 保存新闻情报到数据库（用于后续复盘与查询）
                    try:
                        query_context = self._build_query_context(query_id=query_id)
                        for dim_name, response in intel_results.items():
                            if response and response.success and response.results:
                                self.db.save_news_intel(
                                    code=code,
                                    name=stock_name,
                                    dimension=dim_name,
                                    query=response.query,
                                    response=response,
                                    query_context=query_context
                                )
                    except Exception as e:
                        logger.warning(f"{stock_name}({code}) 保存新闻情报失败: {e}")
            else:
                logger.info(f"{stock_name}({code}) 搜索服务不可用，跳过情报搜索")

            persisted_intelligence_context = self._load_persisted_intelligence_context(
                code=code,
                stock_name=stock_name,
            )
            if persisted_intelligence_context:
                news_context = f"{news_context}\n\n{persisted_intelligence_context}" if news_context else persisted_intelligence_context

            # Step 4.5: 社交舆情情报（仅美股）
            if self.social_sentiment_service is not None and self.social_sentiment_service.is_available and is_us_stock_code(code):
                try:
                    social_context = self.social_sentiment_service.get_social_context(code)
                    if social_context:
                        logger.info(f"{stock_name}({code}) Social sentiment data retrieved")
                        if news_context:
                            news_context = news_context + "\n\n" + social_context
                        else:
                            news_context = social_context
                except Exception as e:
                    logger.warning(f"{stock_name}({code}) Social sentiment fetch failed: {e}")

            # Step 5: 获取分析上下文（技术面数据）
            self._emit_progress(58, f"{stock_name}：正在整理分析上下文")
            context = self.db.get_analysis_context(code)

            if context is None:
                logger.warning(f"{stock_name}({code}) 无法获取历史行情数据，将仅基于新闻和实时行情分析")
                _mkt_date = get_market_now(
                    get_market_for_stock(normalize_stock_code(code))
                ).date()
                context = {
                    'code': code,
                    'stock_name': stock_name,
                    'date': _mkt_date.isoformat(),
                    'data_missing': True,
                    'today': {},
                    'yesterday': {}
                }
            
            # Step 6: 增强上下文数据（添加实时行情、筹码、趋势分析结果、股票名称）
            enhanced_context = self._enhance_context(
                context, 
                realtime_quote, 
                chip_data,
                trend_result,
                stock_name,  # 传入股票名称
                fundamental_context,
            )
            enhanced_context["news_result_count"] = news_result_count
            self._attach_daily_market_context(enhanced_context, daily_market_context)
            enhanced_context["market_structure_context"] = market_structure_context
            enhanced_context[MARKET_PHASE_SUMMARY_KEY] = market_phase_summary
            if self.portfolio_context is not None:
                enhanced_context["portfolio_context"] = dict(self.portfolio_context)
            enhanced_context["analysis_phase"] = self.analysis_phase
            deep_research_profile = self._build_deep_research_stock_profile(
                code,
                stock_name,
                {
                    "stock_code": code,
                    "stock_name": stock_name,
                    "report_language": normalize_report_language(getattr(self.config, "report_language", "zh")),
                    "fundamental_context": fundamental_context,
                    "news_context": news_context,
                    "realtime_quote": self._safe_to_dict(realtime_quote) if realtime_quote else None,
                    "chip_distribution": self._safe_to_dict(chip_data) if chip_data else None,
                    "trend_result": self._safe_to_dict(trend_result) if trend_result else None,
                    "analysis_context": enhanced_context,
                },
            )
            if deep_research_profile:
                enhanced_context["stock_profile"] = deep_research_profile
            
            # Step 7: 调用 AI 分析（传入增强的上下文和新闻）
            llm_progress_state = {"last_progress": 64}

            # 流式回调：按已接收字符数推算进度，使长时间生成阶段不至于看起来卡住
            def _on_llm_stream(chars_received: int) -> None:
                """随 LLM 流式返回的字符数累加，更新任务进度。"""
                # 每 80 字符推进 1 个百分点，上限 28，整体进度封顶 92（留出保存阶段余量）
                dynamic_progress = min(92, 64 + min(chars_received // 80, 28))
                if dynamic_progress <= llm_progress_state["last_progress"]:
                    return
                llm_progress_state["last_progress"] = dynamic_progress
                self._emit_progress(
                    dynamic_progress,
                    f"{stock_name}：LLM 正在生成分析结果（已接收 {chars_received} 字符）",
                )

            self._emit_progress(64, f"{stock_name}：正在请求 LLM 生成报告")
            result = self.analyzer.analyze(
                enhanced_context,
                news_context=news_context,
                progress_callback=self._emit_progress,
                stream_progress_callback=_on_llm_stream,
            )
            if result:
                result.news_result_count = news_result_count
                result.news_evidence_present = news_evidence_present(news_result_count)

            # Step 7.5: 填充分析时的价格信息到 result
            if result:
                self._emit_progress(94, f"{stock_name}：正在校验并整理分析结果")
                result.query_id = query_id
                realtime_data = enhanced_context.get('realtime', {})
                result.current_price = realtime_data.get('price')
                result.change_pct = realtime_data.get('change_pct')

            # Step 7.6: 筹码结构兜底填充（Issue #589）——LLM 未输出时用真实筹码数据补齐
            if result and chip_data:
                fill_chip_structure_if_needed(result, chip_data)

            # Step 7.7: 价格位置兜底填充，并用技术面/基本面稳定最终决策
            if result:
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                from src.schemas.decision_scale import apply_score_action_scale
                apply_score_action_scale(result)
                stabilize_decision_with_structure(result, trend_result, fundamental_context)
                market_adjustments = apply_daily_market_context_guardrail(
                    result,
                    daily_market_context=enhanced_context.get("daily_market_context"),
                    report_language=getattr(result, "report_language", "zh"),
                )
                if market_adjustments:
                    result.decision_guardrails.extend(
                        {"type": "daily_market_context", "adjustment": adjustment}
                        for adjustment in market_adjustments
                    )
                if isinstance(result.dashboard, dict):
                    result.dashboard["decision_action"] = result.decision_action
                    result.dashboard["decision_guardrails"] = list(result.decision_guardrails)
                result.stock_profile = deep_research_profile
                result.market_structure_context = enhanced_context.get("market_structure_context")
                result.market_phase_summary = enhanced_context.get(MARKET_PHASE_SUMMARY_KEY)
                result.fundamental_context = fundamental_context

            # Step 8: 保存分析历史记录
            if result and result.success:
                try:
                    self._emit_progress(97, f"{stock_name}：正在保存分析报告")
                    context_snapshot = self._build_context_snapshot(
                        enhanced_context=enhanced_context,
                        news_content=news_context,
                        realtime_quote=realtime_quote,
                        chip_data=chip_data
                    )
                    context_snapshot["diagnostics"] = current_diagnostic_snapshot()
                    saved_history_id = self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=news_context,
                        context_snapshot=context_snapshot,
                        save_snapshot=self.save_context_snapshot,
                        user_id=self.user_id,
                        return_id=True,
                    )
                    # Persist the structured decision signal alongside the report.
                    # The service is best-effort and idempotent, so a signal failure
                    # must not turn a successful analysis into a failed run.
                    if saved_history_id:
                        try:
                            from src.services.decision_signal_service import DecisionSignalService
                            DecisionSignalService(self.db).sync_analysis_history(user_id=self.user_id, limit=1)
                        except Exception as signal_exc:  # noqa: BLE001
                            logger.warning("%s(%s) decision signal extraction failed: %s", stock_name, code, signal_exc)
                    record_history_run(
                        report_saved=bool(saved_history_id),
                        metadata_saved=bool(saved_history_id),
                        analysis_history_id=saved_history_id if isinstance(saved_history_id, int) else None,
                    )
                except Exception as e:
                    record_history_run(report_saved=False, metadata_saved=False, error_message=e)
                    logger.warning(f"{stock_name}({code}) 保存分析历史失败: {e}")

            return result

        except Exception as e:
            logger.error(f"{stock_name}({code}) 分析失败: {e}")
            logger.exception(f"{stock_name}({code}) 详细错误信息:")
            return None
    
    def _enhance_context(
        self,
        context: Dict[str, Any],
        realtime_quote,
        chip_data: Optional[ChipDistribution],
        trend_result: Optional[TrendAnalysisResult],
        stock_name: str = "",
        fundamental_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        增强分析上下文
        
        将实时行情、筹码分布、趋势分析结果、股票名称添加到上下文中
        
        Args:
            context: 原始上下文
            realtime_quote: 实时行情数据（UnifiedRealtimeQuote 或 None）
            chip_data: 筹码分布数据
            trend_result: 趋势分析结果
            stock_name: 股票名称
            
        Returns:
            增强后的上下文
        """
        enhanced = context.copy()
        enhanced["report_language"] = normalize_report_language(getattr(self.config, "report_language", "zh"))
        
        # 添加股票名称
        if stock_name:
            enhanced['stock_name'] = stock_name
        elif realtime_quote and getattr(realtime_quote, 'name', None):
            enhanced['stock_name'] = realtime_quote.name

        # 将运行时搜索窗口透传给 analyzer，避免与全局配置重新读取产生窗口不一致
        enhanced['news_window_days'] = getattr(self.search_service, "news_window_days", 3)
        
        # 添加实时行情（兼容不同数据源的字段差异）
        if realtime_quote:
            # 使用 getattr 安全获取字段，缺失字段返回 None 或默认值
            volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
            enhanced['realtime'] = {
                'name': getattr(realtime_quote, 'name', ''),
                'price': getattr(realtime_quote, 'price', None),
                'change_pct': getattr(realtime_quote, 'change_pct', None),
                'volume_ratio': volume_ratio,
                'volume_ratio_desc': self._describe_volume_ratio(volume_ratio) if volume_ratio else '无数据',
                'turnover_rate': getattr(realtime_quote, 'turnover_rate', None),
                'pe_ratio': getattr(realtime_quote, 'pe_ratio', None),
                'pb_ratio': getattr(realtime_quote, 'pb_ratio', None),
                'total_mv': getattr(realtime_quote, 'total_mv', None),
                'circ_mv': getattr(realtime_quote, 'circ_mv', None),
                'change_60d': getattr(realtime_quote, 'change_60d', None),
                'source': getattr(realtime_quote, 'source', None),
            }
            # 移除 None 值以减少上下文大小
            enhanced['realtime'] = {k: v for k, v in enhanced['realtime'].items() if v is not None}
        
        # 添加筹码分布
        if chip_data:
            current_price = getattr(realtime_quote, 'price', 0) if realtime_quote else 0
            enhanced['chip'] = {
                'profit_ratio': chip_data.profit_ratio,
                'avg_cost': chip_data.avg_cost,
                'concentration_90': chip_data.concentration_90,
                'concentration_70': chip_data.concentration_70,
                'chip_status': chip_data.get_chip_status(current_price or 0),
            }
        
        # 添加趋势分析结果
        if trend_result:
            enhanced['trend_analysis'] = {
                'trend_status': trend_result.trend_status.value,
                'ma_alignment': trend_result.ma_alignment,
                'trend_strength': trend_result.trend_strength,
                'bias_ma5': trend_result.bias_ma5,
                'bias_ma10': trend_result.bias_ma10,
                'volume_status': trend_result.volume_status.value,
                'volume_trend': trend_result.volume_trend,
                'buy_signal': trend_result.buy_signal.value,
                'signal_score': trend_result.signal_score,
                'signal_reasons': trend_result.signal_reasons,
                'risk_factors': trend_result.risk_factors,
            }

        # Issue #234：盘中用实时 OHLC 覆盖 today，并用趋势分析算出的均线替换库内均线。
        # 守卫条件 trend_result.ma5 > 0 用于确认均线确实算出来了（历史数据充足），
        # 否则用实时价覆盖昨收口径的 today 反而会引入错误的技术指标。
        if realtime_quote and trend_result and trend_result.ma5 > 0:
            price = getattr(realtime_quote, 'price', None)
            if price is not None and price > 0:
                yesterday_close = None
                if enhanced.get('yesterday') and isinstance(enhanced['yesterday'], dict):
                    yesterday_close = enhanced['yesterday'].get('close')
                orig_today = enhanced.get('today') or {}
                open_p = getattr(realtime_quote, 'open_price', None) or getattr(
                    realtime_quote, 'pre_close', None
                ) or yesterday_close or orig_today.get('open') or price
                high_p = getattr(realtime_quote, 'high', None) or price
                low_p = getattr(realtime_quote, 'low', None) or price
                vol = getattr(realtime_quote, 'volume', None)
                amt = getattr(realtime_quote, 'amount', None)
                pct = getattr(realtime_quote, 'change_pct', None)
                realtime_today = {
                    'close': price,
                    'open': open_p,
                    'high': high_p,
                    'low': low_p,
                    'ma5': trend_result.ma5,
                    'ma10': trend_result.ma10,
                    'ma20': trend_result.ma20,
                }
                if vol is not None:
                    realtime_today['volume'] = vol
                if amt is not None:
                    realtime_today['amount'] = amt
                if pct is not None:
                    realtime_today['pct_chg'] = pct
                for k, v in orig_today.items():
                    if k not in realtime_today and v is not None:
                        realtime_today[k] = v
                enhanced['today'] = realtime_today
                enhanced['ma_status'] = self._compute_ma_status(
                    price, trend_result.ma5, trend_result.ma10, trend_result.ma20
                )
                enhanced['date'] = get_market_now(
                    get_market_for_stock(normalize_stock_code(enhanced.get('code', '')))
                ).date().isoformat()
                if yesterday_close is not None:
                    try:
                        yc = float(yesterday_close)
                        if yc > 0:
                            enhanced['price_change_ratio'] = round(
                                (price - yc) / yc * 100, 2
                            )
                    except (TypeError, ValueError):
                        pass
                if vol is not None and enhanced.get('yesterday'):
                    yest_vol = enhanced['yesterday'].get('volume') if isinstance(
                        enhanced['yesterday'], dict
                    ) else None
                    if yest_vol is not None:
                        try:
                            yv = float(yest_vol)
                            if yv > 0:
                                enhanced['volume_change_ratio'] = round(
                                    float(vol) / yv, 2
                                )
                        except (TypeError, ValueError):
                            pass

        # ETF/指数标记，供 analyzer 的提示词区分指数型标的（Fixes #274）
        enhanced['is_index_etf'] = SearchService.is_index_or_etf(
            context.get('code', ''), enhanced.get('stock_name', stock_name)
        )

        # P0：统一附加基本面数据块，仅作为额外上下文，不改变既有技术面/新闻字段
        enhanced["fundamental_context"] = (
            fundamental_context
            if isinstance(fundamental_context, dict)
            else self.fetcher_manager.build_failed_fundamental_context(
                context.get("code", ""),
                "invalid fundamental context",
            )
        )

        return enhanced

    def _load_daily_market_context(
        self,
        market: str,
        *,
        target_date: Optional[date] = None,
    ) -> Optional[DailyMarketContext]:
        """加载共享的 A股大盘上下文，且不阻塞个股分析流程。"""
        if market != "cn" or not getattr(self, "daily_market_context_enabled", False):
            return None
        try:
            service = getattr(self, "_daily_market_context_service", None)
            if service is None:
                service_lock = getattr(self, "_daily_market_context_service_lock", None)
                if service_lock is None:
                    service_lock = threading.Lock()
                    self._daily_market_context_service_lock = service_lock
                with service_lock:
                    service = getattr(self, "_daily_market_context_service", None)
                    if service is None:
                        service = DailyMarketContextService(db_manager=self.db)
                        self._daily_market_context_service = service
            context_date = target_date or get_effective_trading_date(
                market,
                current_time=datetime.now(timezone.utc),
            )
            return service.get_context(
                region="cn",
                config=self.config,
                notifier=self.notifier,
                analyzer=self.analyzer,
                search_service=self.search_service,
                allow_generate=bool(getattr(self, "daily_market_context_allow_generate", True)),
                persist_market_review_history=False,
                target_date=context_date,
                current_query_id=getattr(self, "query_id", None),
            )
        except Exception as exc:
            logger.warning("%s A股大盘上下文获取失败，继续原分析流程: %s", market, exc)
            return None

    @staticmethod
    def _attach_daily_market_context(
        target_context: Dict[str, Any],
        daily_market_context: Optional[DailyMarketContext],
    ) -> None:
        """把 A股大盘上下文挂载到个股分析上下文中。

        同时写入结构化字典与已格式化的提示词段落：前者供护栏（guardrail）逻辑读取，
        后者供 LLM 提示词直接拼接使用。
        """
        if daily_market_context is None:
            return
        safe_context = daily_market_context.to_safe_dict()
        target_context["daily_market_context"] = safe_context
        target_context["daily_market_context_summary"] = format_daily_market_context_prompt_section(
            safe_context,
            report_language=str(target_context.get("report_language") or "zh"),
        )

    def _attach_belong_boards_to_fundamental_context(
        self,
        code: str,
        fundamental_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        将 A股所属板块（belong_boards）作为顶层补充字段附加到基本面上下文。

        采用浅拷贝，避免取出后的缓存基本面上下文被就地（in place）修改。
        """
        if isinstance(fundamental_context, dict):
            enriched_context = dict(fundamental_context)
        else:
            enriched_context = self.fetcher_manager.build_failed_fundamental_context(
                code,
                "invalid fundamental context",
            )

        existing_boards = enriched_context.get("belong_boards")
        if isinstance(existing_boards, list):
            enriched_context["belong_boards"] = list(existing_boards)
            return enriched_context

        boards_block = enriched_context.get("boards")
        boards_status = boards_block.get("status") if isinstance(boards_block, dict) else None
        coverage = enriched_context.get("coverage")
        boards_coverage = coverage.get("boards") if isinstance(coverage, dict) else None
        market = enriched_context.get("market")
        if not isinstance(market, str) or not market.strip():
            market = get_market_for_stock(normalize_stock_code(code))

        if (
            market != "cn"
            or boards_status == "not_supported"
            or boards_coverage == "not_supported"
        ):
            enriched_context["belong_boards"] = []
            return enriched_context

        boards: List[Dict[str, Any]] = []
        try:
            raw_boards = self.fetcher_manager.get_belong_boards(code)
            if isinstance(raw_boards, list):
                boards = raw_boards
        except Exception as e:
            logger.debug("%s attach belong_boards failed (fail-open): %s", code, e)

        enriched_context["belong_boards"] = boards
        return enriched_context

    def _build_market_structure_context(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        fundamental_context: Optional[Dict[str, Any]],
        daily_market_context: Optional[DailyMarketContext] = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """构建共享的 A股市场结构上下文，采用 fail-open（放行）策略。"""
        if str(market or "").strip().lower() != "cn":
            return self._market_structure_not_supported_context(
                code=code,
                stock_name=stock_name,
                market=market,
            )
        if isinstance(fundamental_context, dict) and str(
            fundamental_context.get("status") or ""
        ).strip().lower() in {"failed", "error"}:
            # 上游基本面阶段已失败时不再发起额外的排名请求：既保持主流程的 fail-open 行为，
            # 也避免明知会超时的数据提供方调用拖慢整体分析。
            return {}
        service = getattr(self, "market_structure_service", None)
        if service is None:
            try:
                service = MarketStructureService(fetcher_manager=self.fetcher_manager)
                self.market_structure_service = service
            except Exception as exc:
                logger.debug("%s market structure service init failed: %s", code, exc)
                return {}
        try:
            trade_date = None
            if isinstance(fundamental_context, dict):
                trade_date = fundamental_context.get("trade_date") or fundamental_context.get("date")
            if trade_date is None and daily_market_context is not None:
                trade_date = getattr(daily_market_context, "trade_date", None)
            return service.build_context(
                code=code,
                stock_name=stock_name,
                market=market,
                fundamental_context=fundamental_context,
                trade_date=trade_date,
                market_phase_summary=market_phase_summary,
            )
        except Exception as exc:
            logger.warning("%s market structure context failed (fail-open): %s", code, exc)
            return {}

    @staticmethod
    def _market_structure_not_supported_context(
        *, code: str, stock_name: str, market: str
    ) -> Dict[str, Any]:
        """返回稳定的"非 A股"标记结构，且不调用任何 A股数据提供方。"""
        return {
            "schema_version": "market-structure-v1",
            "status": "not_supported",
            "market": str(market or "").strip().lower() or "unknown",
            "market_theme_context": {
                "schema_version": "market-theme-v1",
                "status": "not_supported",
                "market": str(market or "").strip().lower() or "unknown",
                "active_themes": [],
                "leading_industries": [],
                "leading_concepts": [],
                "lagging_themes": [],
                "theme_breadth": {
                    "active_count": 0,
                    "leading_industry_count": 0,
                    "leading_concept_count": 0,
                    "lagging_count": 0,
                },
                "data_quality": {
                    "status": "not_supported",
                    "missing_fields": ["a_share_theme_context"],
                    "sources": [],
                    "errors": [],
                },
            },
            "stock_market_position": {
                "schema_version": "stock-market-position-v1",
                "status": "not_supported",
                "stock_code": code,
                "stock_name": stock_name,
                "market": str(market or "").strip().lower() or "unknown",
                "related_boards": [],
                "stock_role": "unknown",
                "theme_phase": "unknown",
                "risk_tags": [],
                "missing_fields": ["a_share_theme_context"],
            },
        }

    def _ensure_agent_history(self, code: str, min_days: int = 240) -> None:
        """确保数据库中存在至少 *min_days* 根 K 线历史，供 Agent 工具调用。"""
        from src.services.history_loader import get_frozen_target_date

        target = get_frozen_target_date()
        if target is None:
            target = self._resolve_resume_target_date(code)
        # 回看窗口按目标交易日数的 1.8 倍折算为自然日，覆盖周末与休市日
        start = target - timedelta(days=int(min_days * 1.8))
        bars = self.db.get_data_range(code, start, target)
        if bars and len(bars) >= min(min_days, 200):
            logger.debug("[%s] Agent history: %d bars in DB, sufficient", code, len(bars))
            return
        try:
            df, source = self.fetcher_manager.get_daily_data(code, days=min_days)
            if df is not None and not df.empty:
                self.db.save_daily_data(df, code, source)
                logger.info("[%s] Prefetched %d rows of history for agent (source: %s)", code, len(df), source)
        except Exception as e:
            logger.warning("[%s] Agent history prefetch failed: %s", code, e)

    def _load_persisted_intelligence_context(
        self,
        *,
        code: str,
        stock_name: str,
        limit: int = 6,
    ) -> Optional[str]:
        """先取 A 股个股证据再取市场证据，全程不阻塞分析主流程。"""
        if not _a_share_intelligence_scope_values(code):
            return None
        try:
            from src.services.intelligence_service import IntelligenceService

            service = IntelligenceService(config=self.config)
            service.refresh_auto_sources()
            days = max(1, int(self.config.get_effective_news_window_days()))
            collected: List[Dict[str, Any]] = []
            seen_urls: set[str] = set()
            scopes = [
                {"scope_type": "symbol", "scope_value": value}
                for value in _a_share_intelligence_scope_values(code)
            ] + [{"scope_type": "market"}]
            for filters in scopes:
                payload = service.list_items(days=days, page=1, page_size=limit, **filters)
                for item in payload.get("items", []):
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("url") or item.get("title") or "").strip()
                    if not key or key in seen_urls:
                        continue
                    seen_urls.add(key)
                    collected.append(item)
                    if len(collected) >= limit:
                        break
                if len(collected) >= limit:
                    break
            if not collected:
                return None

            lines = [f"## 本地资讯证据池（{stock_name}/{code}）"]
            for index, item in enumerate(collected, 1):
                title = str(item.get("title") or "未命名资讯").strip()
                summary = str(item.get("summary") or "").strip()
                source = str(item.get("source") or item.get("source_name") or "本地资讯").strip()
                published = str(item.get("published_at") or "").strip()
                meta = " / ".join(part for part in (source, published) if part)
                lines.append(f"{index}. {title}" + (f"（{meta}）" if meta else ""))
                if summary:
                    lines.append(f"   摘要：{summary[:220]}")
            return "\n".join(lines)
        except Exception as exc:
            logger.debug("读取本地资讯证据失败，继续分析: %s", exc)
            return None

    def _analyze_with_agent(
        self, 
        code: str, 
        report_type: ReportType, 
        query_id: str,
        stock_name: str,
        realtime_quote: Any,
        chip_data: Optional[ChipDistribution],
        fundamental_context: Optional[Dict[str, Any]] = None,
        trend_result: Optional[TrendAnalysisResult] = None,
        daily_market_context: Optional[DailyMarketContext] = None,
        market_structure_context: Optional[Dict[str, Any]] = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
    ) -> Optional[AnalysisResult]:
        """
        使用 Agent 模式分析单只股票。

        与传统路径的区别：由 Agent executor 自行编排工具调用，本方法负责预置上下文、
        执行后把 AgentResult 归一化为 AnalysisResult，并补齐筹码/价格位置/大盘护栏等
        兜底字段后落库。全部异常在本方法内收敛，失败返回 ``None``。
        """
        try:
            from src.agent.factory import build_agent_executor
            report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))

            requested_skills = (
                self.analysis_skills
                if self.analysis_skills is not None
                else (getattr(self.config, 'agent_skills', None) or None)
            )
            # 通过共享工厂构建 executor（ToolRegistry 与 SkillManager 原型在其中被缓存复用）
            executor = build_agent_executor(self.config, requested_skills, user_id=self.user_id)

            # 预置初始上下文，避免 Agent 再重复调用工具取这些已知数据
            initial_context = {
                "stock_code": code,
                "stock_name": stock_name,
                "report_type": report_type.value,
                "report_language": report_language,
                "fundamental_context": fundamental_context,
                "analysis_phase": self.analysis_phase,
                "market_structure_context": market_structure_context,
                MARKET_PHASE_SUMMARY_KEY: market_phase_summary,
            }
            if self.portfolio_context is not None:
                initial_context["portfolio_context"] = dict(self.portfolio_context)
            if self.analysis_skills is not None:
                initial_context["skills"] = self.analysis_skills
            self._attach_daily_market_context(initial_context, daily_market_context)
            
            if realtime_quote:
                initial_context["realtime_quote"] = self._safe_to_dict(realtime_quote)
            if chip_data:
                initial_context["chip_distribution"] = self._safe_to_dict(chip_data)
            if trend_result:
                initial_context["trend_result"] = self._safe_to_dict(trend_result)

            persisted_intelligence_context = self._load_persisted_intelligence_context(
                code=code,
                stock_name=stock_name,
            )
            if persisted_intelligence_context:
                initial_context["news_context"] = persisted_intelligence_context
                logger.info("[%s] Agent mode: local intelligence evidence injected into news_context", code)

            # Agent 路径：把社交舆情注入 news_context，这样 executor（_build_user_message）
            # 与 orchestrator（ctx.set_data）都能沿用既有的 news_context 通道消费它
            if self.social_sentiment_service is not None and self.social_sentiment_service.is_available and is_us_stock_code(code):
                try:
                    social_context = self.social_sentiment_service.get_social_context(code)
                    if social_context:
                        existing = initial_context.get("news_context")
                        if existing:
                            initial_context["news_context"] = existing + "\n\n" + social_context
                        else:
                            initial_context["news_context"] = social_context
                        logger.info(f"[{code}] Agent mode: social sentiment data injected into news_context")
                except Exception as e:
                    logger.warning(f"[{code}] Agent mode: social sentiment fetch failed: {e}")

            # Issue #1066：Agent 工具会直接查库取历史，必须先确保深度历史已落库
            self._ensure_agent_history(code)
            deep_research_profile = self._build_deep_research_stock_profile(
                code,
                stock_name,
                initial_context,
            )
            if deep_research_profile:
                initial_context["stock_profile"] = deep_research_profile

            # 运行 Agent
            if report_language == "en":
                message = f"Analyze stock {code} ({stock_name}) and return the full decision dashboard JSON in English."
            else:
                message = f"请分析股票 {code} ({stock_name})，并生成决策仪表盘报告。"
            news_evidence_token = activate_news_evidence_scope()
            news_evidence = get_current_news_evidence()
            try:
                agent_result = executor.run(message, context=initial_context)
            finally:
                reset_news_evidence_scope(news_evidence_token)

            # 转换为 AnalysisResult
            result = self._agent_result_to_analysis_result(
                agent_result,
                code,
                stock_name,
                report_type,
                query_id,
                trend_result=trend_result,
            )
            if result and news_evidence is not None:
                result.news_result_count = news_evidence.resolve(
                    search_available=bool(
                        self.search_service is not None and self.search_service.is_available
                    )
                )
                result.news_evidence_present = news_evidence_present(result.news_result_count)
                initial_context["news_result_count"] = result.news_result_count
            if result:
                result.query_id = query_id
            # Agent 弱完整性模式：仅做占位补全，不触发 LLM 重试（重试成本高且不稳定）
            if result and getattr(self.config, "report_integrity_enabled", False):
                from src.analyzer import check_content_integrity, apply_placeholder_fill

                pass_integrity, missing = check_content_integrity(result)
                if not pass_integrity:
                    apply_placeholder_fill(result, missing)
                    logger.info(
                        "[LLM完整性] integrity_mode=agent_weak 必填字段缺失 %s，已占位补全",
                        missing,
                    )
            # 筹码结构兜底（Issue #589），必须早于 save_analysis_history 执行
            if result and chip_data:
                fill_chip_structure_if_needed(result, chip_data)

            # 价格位置兜底（与非 Agent 路径的 Step 7.7 保持一致）
            if result:
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                realtime_data = initial_context.get("realtime_quote", {})
                if isinstance(realtime_data, dict):
                    result.current_price = realtime_data.get("price")
                    result.change_pct = realtime_data.get("change_pct")
                from src.schemas.decision_scale import apply_score_action_scale
                apply_score_action_scale(result)
                stabilize_decision_with_structure(result, trend_result, fundamental_context)
                market_adjustments = apply_daily_market_context_guardrail(
                    result,
                    daily_market_context=initial_context.get("daily_market_context"),
                    report_language=getattr(result, "report_language", "zh"),
                )
                if market_adjustments:
                    result.decision_guardrails.extend(
                        {"type": "daily_market_context", "adjustment": adjustment}
                        for adjustment in market_adjustments
                    )
                if isinstance(result.dashboard, dict):
                    result.dashboard["decision_action"] = result.decision_action
                    result.dashboard["decision_guardrails"] = list(result.decision_guardrails)
                result.stock_profile = deep_research_profile
                result.market_structure_context = initial_context.get("market_structure_context")
                result.market_phase_summary = initial_context.get(MARKET_PHASE_SUMMARY_KEY)
                result.fundamental_context = fundamental_context

            resolved_stock_name = result.name if result and result.name else stock_name

            # 保存新闻情报到数据库（Agent 工具结果仅用于 LLM 上下文，未持久化，Fixes #396）
            # 使用 search_stock_news（与 Agent 工具调用逻辑一致），仅 1 次 API 调用，无额外延迟
            if self.search_service is not None and self.search_service.is_available:
                try:
                    news_response = self.search_service.search_stock_news(
                        stock_code=code,
                        stock_name=resolved_stock_name,
                        max_results=5
                    )
                    if news_response.success and news_response.results:
                        query_context = self._build_query_context(query_id=query_id)
                        self.db.save_news_intel(
                            code=code,
                            name=resolved_stock_name,
                            dimension="latest_news",
                            query=news_response.query,
                            response=news_response,
                            query_context=query_context
                        )
                        logger.info(f"[{code}] Agent 模式: 新闻情报已保存 {len(news_response.results)} 条")
                except Exception as e:
                    logger.warning(f"[{code}] Agent 模式保存新闻情报失败: {e}")

            # 保存分析历史记录
            if result and result.success:
                try:
                    initial_context["stock_name"] = resolved_stock_name
                    persisted_agent_context = dict(initial_context)
                    persisted_agent_context.pop("portfolio_context", None)
                    persisted_agent_context["diagnostics"] = current_diagnostic_snapshot()
                    saved_history_id = self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=None,
                        context_snapshot=persisted_agent_context,
                        save_snapshot=self.save_context_snapshot,
                        user_id=self.user_id,
                        return_id=True,
                    )
                    skill_opinions = getattr(agent_result, "skill_opinions", [])
                    if saved_history_id and skill_opinions:
                        from src.services.skill_opinion_sample_service import SkillOpinionSampleService

                        SkillOpinionSampleService().persist(
                            analysis_history_id=saved_history_id,
                            stock_code=code,
                            opinions=skill_opinions,
                        )
                except Exception as e:
                    logger.warning(f"[{code}] 保存 Agent 分析历史失败: {e}")

            return result

        except Exception as e:
            logger.error(f"[{code}] Agent 分析失败: {e}")
            logger.exception(f"[{code}] Agent 详细错误信息:")
            return None

    def _is_deep_research_profile_requested(self) -> bool:
        """判断是否在生成报告前运行 Deep Research 以产出股票基本情况。"""
        is_available = getattr(self.config, "is_agent_available", None)
        if callable(is_available):
            return bool(is_available())
        return bool(getattr(self.config, "agent_mode", False))

    def _build_deep_research_stock_profile(
        self,
        code: str,
        stock_name: str,
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """在最终报告前生成详细的股票基本情况（stock profile）章节。

        这是一条可选的增强路径。若 Agent 模式不可用则返回 ``None``；若已请求 Agent 模式
        但执行中失败，则向上抛出异常，交由调用方决定是否以更轻量的报告继续。
        """
        if not self._is_deep_research_profile_requested():
            return None

        try:
            is_available = getattr(self.config, "is_agent_available", None)
            if callable(is_available) and not is_available():
                raise RuntimeError("Agent/LLM capability is unavailable")

            from src.agent.factory import get_tool_registry
            from src.agent.llm_adapter import LLMToolAdapter
            from src.agent.research import ResearchAgent

            report_language = normalize_report_language(
                context.get("report_language")
                or getattr(self.config, "report_language", "zh")
            )
            if report_language == "en":
                query = (
                    f"Create a comprehensive and detailed Deep Research stock profile for {stock_name} ({code}). "
                    "Cover company overview, business lines, revenue/profit drivers, business model, products, customers, supply chain, "
                    "competitive moat, industry structure, market position, shareholder/management context, financial quality, valuation context, "
                    "recent catalysts, key risks, source clues, unresolved unknowns, and implications for the final investment report. "
                    "Use only verifiable tool/context information and mark unknowns explicitly. "
                    "Output only the final stock profile body that can be displayed directly in a report. "
                    "Do not include follow-up offers, task lists, template suggestions, process explanations, or meta statements such as "
                    "'I can next', 'if you want', 'would you like', or 'here is a template'."
                )
            else:
                query = (
                    f"为 {stock_name}（{code}）生成一份尽可能详尽的 Deep Research 股票基本情况。"
                    "覆盖公司概况、主营业务、收入与利润驱动、商业模式、核心产品、客户与供应链、竞争壁垒、行业格局、市场地位、"
                    "股东与管理层背景、财务质量、估值背景、近期催化、关键风险、来源线索、尚未确认的未知项，以及对最终投资报告的影响。"
                    "只使用工具或上下文中可验证的信息，缺失内容请明确说明未知。"
                    "只输出可直接展示在最终报告里的股票基本情况正文。"
                    "禁止输出“如果你愿意”“我可以下一步”“我可以帮你整理成模板”“包括如下内容”等对话式收尾、模板说明、待办清单、过程解释或元话术。"
                )

            try:
                budget = int(getattr(self.config, "agent_deep_research_budget", 30000) or 30000)
            except (TypeError, ValueError):
                budget = 30000
            try:
                timeout = int(getattr(self.config, "agent_deep_research_timeout", 600) or 600)
            except (TypeError, ValueError):
                timeout = 600
            try:
                max_sub_questions = int(getattr(self.config, "agent_deep_research_max_sub_questions", 8) or 8)
            except (TypeError, ValueError):
                max_sub_questions = 8
            try:
                sub_question_steps = int(getattr(self.config, "agent_deep_research_sub_question_steps", 6) or 6)
            except (TypeError, ValueError):
                sub_question_steps = 6
            budget = max(budget, 5000)
            timeout = max(timeout, 30)
            max_sub_questions = max(max_sub_questions, 1)
            sub_question_steps = max(sub_question_steps, 1)
            self._emit_progress(60, f"{stock_name}：正在执行 Deep Research 生成股票基本情况")
            agent = ResearchAgent(
                tool_registry=get_tool_registry(),
                llm_adapter=LLMToolAdapter(self.config, user_id=getattr(self, "user_id", None)),
                token_budget=budget,
                max_sub_questions=max_sub_questions,
                sub_question_max_steps=sub_question_steps,
            )
            research = agent.research(query, context=context, timeout_seconds=timeout)
            if not research.success or not research.report.strip():
                raise RuntimeError(research.error or "Deep Research returned empty stock profile")

            return {
                "research_report": self._clean_stock_profile_report(research.report),
                "research_method": "deep_research",
                "research_sources": [str(item) for item in research.sub_questions if item],
                "research_token_usage": research.total_tokens,
            }
        except Exception as e:
            logger.error("[%s] Deep Research stock profile generation failed before report: %s", code, e)
            raise

    @classmethod
    def _clean_stock_profile_report(cls, report: str) -> str:
        """剔除股票基本情况正文里的 LLM 元话术/跟进话术行（meta/follow-up）。"""
        text = str(report or "").strip()
        if not text:
            return ""
        lines = text.splitlines()
        kept_lines: List[str] = []
        for line in lines:
            if cls._STOCK_PROFILE_META_LINE_PATTERN.search(line.strip()):
                break
            kept_lines.append(line)
        cleaned = "\n".join(kept_lines).strip()
        return cleaned

    def _agent_result_to_analysis_result(
        self,
        agent_result,
        code: str,
        stock_name: str,
        report_type: ReportType,
        query_id: str,
        trend_result: Optional[TrendAnalysisResult] = None,
    ) -> AnalysisResult:
        """
        将 AgentResult 转换为 AnalysisResult。

        Agent 成功且带 dashboard 时逐字段取值，缺失或为占位符时用技术分析（trend_result）
        的兜底值补齐并标记数据源；Agent 失败时整条走技术面兜底路径，保证下游仍拿到可展示结果。
        """
        report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))
        result = AnalysisResult(
            code=code,
            name=stock_name,
            sentiment_score=50,
            trend_prediction="Unknown" if report_language == "en" else "未知",
            operation_advice="Watch" if report_language == "en" else "观望",
            confidence_level=localize_confidence_level("medium", report_language),
            report_language=report_language,
            success=agent_result.success,
            error_message=agent_result.error or None,
            data_sources=f"agent:{agent_result.provider}",
            model_used=agent_result.model or None,
        )

        if agent_result.success and agent_result.dashboard:
            dash = agent_result.dashboard
            ai_stock_name = str(dash.get("stock_name", "")).strip()
            if ai_stock_name and self._is_placeholder_stock_name(stock_name, code):
                result.name = ai_stock_name

            nested_dashboard = dash.get("dashboard") if isinstance(dash, dict) else None

            raw_score = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "sentiment_score",
                scalar=True,
            )
            if self._is_agent_field_missing(raw_score, scalar=True):
                fallback_score = self._trend_score_fallback(trend_result)
                if fallback_score is not None:
                    result.sentiment_score = fallback_score
                    self._mark_trend_fallback_source(result)
            else:
                result.sentiment_score = self._safe_int(raw_score, 50)

            raw_trend = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "trend_prediction",
                scalar=True,
                expect_text=True,
            )
            if self._is_agent_field_missing(raw_trend, scalar=True, expect_text=True):
                trend_label = self._trend_label_fallback(
                    trend_result,
                    report_language,
                )
                if trend_label:
                    result.trend_prediction = trend_label
                    self._mark_trend_fallback_source(result)
            else:
                result.trend_prediction = str(raw_trend)

            raw_advice = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "operation_advice",
                scalar=True,
                allow_dict=True,
                expect_text=True,
            )
            extracted_advice = ""
            if isinstance(raw_advice, dict):
                # LLM 可能按持仓状态返回 {"no_position": "...", "has_position": "..."} 的结构化建议
                extracted_advice = self._extract_advice_text_from_dict(raw_advice)
                if extracted_advice:
                    result.operation_advice = localize_operation_advice(
                        extracted_advice,
                        report_language,
                    )
                else:
                    signal_label = self._trend_signal_fallback(
                        trend_result,
                        report_language,
                    )
                    if signal_label:
                        result.operation_advice = signal_label
                        self._mark_trend_fallback_source(result)
            elif not self._is_agent_field_missing(
                raw_advice,
                scalar=True,
                allow_dict=True,
                expect_text=True,
            ):
                result.operation_advice = str(raw_advice) if raw_advice else ("Watch" if report_language == "en" else "观望")
            else:
                signal_label = self._trend_signal_fallback(trend_result, report_language)
                if signal_label:
                    result.operation_advice = signal_label
                    self._mark_trend_fallback_source(result)
            from src.agent.protocols import normalize_decision_signal

            raw_decision = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "decision_type",
                scalar=True,
                expect_text=True,
            )
            if self._is_agent_field_missing(raw_decision, scalar=True, expect_text=True):
                trend_decision = self._trend_decision_fallback(trend_result)
                decision_from_advice = infer_decision_type_from_advice(
                    result.operation_advice,
                    default="",
                )
                if decision_from_advice:
                    result.decision_type = decision_from_advice
                    if (
                        self._is_agent_field_missing(
                            raw_advice,
                            scalar=True,
                            allow_dict=True,
                            expect_text=True,
                        )
                        and not extracted_advice
                        and trend_decision
                    ):
                        self._mark_trend_fallback_source(result)
                else:
                    result.decision_type = trend_decision or "hold"
                    if trend_decision:
                        self._mark_trend_fallback_source(result)
            else:
                result.decision_type = normalize_decision_signal(raw_decision)
            result.confidence_level = localize_confidence_level(
                self._agent_dashboard_value(dash, nested_dashboard, "confidence_level")
                or result.confidence_level,
                report_language,
            )
            raw_stock_profile = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "stock_profile",
                allow_dict=True,
            )
            if isinstance(raw_stock_profile, dict):
                result.stock_profile = raw_stock_profile
            raw_summary = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "analysis_summary",
                scalar=True,
                expect_text=True,
            )
            if not self._is_agent_field_missing(raw_summary, scalar=True, expect_text=True):
                result.analysis_summary = str(raw_summary)
            else:
                result.analysis_summary = self._summary_fallback_from_result(result, report_language)
            # AI 返回的顶层字典里可能还嵌了一层 dashboard 子键（含 core_conclusion /
            # battle_plan / intelligence）。AnalysisResult 的辅助方法（get_sniper_points、
            # get_core_conclusion 等）期望的正是这层内部结构，因此在这里做一次解包。
            result.dashboard = nested_dashboard or dash
            self._backfill_agent_dashboard_fields(result, trend_result, report_language)
        else:
            self._apply_trend_fallback(result, trend_result, report_language)
            if trend_result is not None:
                result.analysis_summary = (
                    result.analysis_summary
                    or self._summary_fallback_from_result(result, report_language)
                )
                self._backfill_agent_dashboard_fields(result, trend_result, report_language)
            if not result.error_message:
                result.error_message = "Agent failed to generate a valid decision dashboard" if report_language == "en" else "Agent 未能生成有效的决策仪表盘"

        return result

    @staticmethod
    def _agent_dashboard_value(
        dash: Dict[str, Any],
        nested_dashboard: Any,
        key: str,
        *,
        scalar: bool = False,
        allow_dict: bool = False,
        expect_text: bool = False,
    ) -> Any:
        """先从顶层 Agent 负载读取标量，缺失时回退到嵌套 dashboard 同名键。"""
        value = dash.get(key) if isinstance(dash, dict) else None
        if isinstance(nested_dashboard, dict) and StockAnalysisPipeline._is_agent_field_missing(
            value,
            scalar=scalar,
            allow_dict=allow_dict,
            expect_text=expect_text,
        ):
            nested_value = nested_dashboard.get(key)
            if not StockAnalysisPipeline._is_agent_field_missing(
                nested_value,
                scalar=scalar,
                allow_dict=allow_dict,
                expect_text=expect_text,
            ):
                value = nested_value
        return value

    @staticmethod
    def _extract_advice_text_from_dict(raw_advice: dict) -> str:
        """从结构化的 Agent 负载中取第一条可用的操作建议文本。"""
        for field in ("has_position", "no_position"):
            if isinstance(raw_advice.get(field), str):
                text = raw_advice[field].strip()
                if not StockAnalysisPipeline._is_agent_placeholder_text(text):
                    return text

        for value in raw_advice.values():
            if isinstance(value, str):
                text = value.strip()
                if not StockAnalysisPipeline._is_agent_placeholder_text(text):
                    return text

        return ""

    @staticmethod
    def _is_agent_placeholder_text(text: str) -> bool:
        """判断 Agent 输出是否为占位符（placeholder）而非真实取值。"""
        if not text:
            return True
        return text.lower() in {"n/a", "na", "none", "null", "unknown", "tbd"} or text in {
            "未知",
            "待补充",
            "数据缺失",
            "无",
        }

    @staticmethod
    def _is_agent_field_missing(
        value: Any,
        *,
        scalar: bool = False,
        allow_dict: bool = False,
        expect_text: bool = False,
    ) -> bool:
        """校验某个 Agent dashboard 字段是否需要回填（backfill）。

        Agent 提供方可能返回顶层标量、嵌套 dashboard 字段、针对持仓的结构化字典或占位符字符串。
        该辅助函数集中处理这些形状判断，使各结果字段的兜底行为保持一致。
        """
        if scalar and isinstance(value, dict):
            if not allow_dict or not value:
                return True
            return not StockAnalysisPipeline._extract_advice_text_from_dict(value)
        if value is None:
            return True
        if expect_text and scalar:
            if not isinstance(value, str):
                return True
        if isinstance(value, str):
            text = value.strip()
            return StockAnalysisPipeline._is_agent_placeholder_text(text)
        if isinstance(value, dict):
            if scalar:
                return not allow_dict
            return not value
        if scalar and isinstance(value, (list, tuple, set)):
            return True
        return False

    @staticmethod
    def _trend_score_fallback(trend_result: Optional[TrendAnalysisResult]) -> Optional[int]:
        """当 Agent 评分缺失时，使用技术分析的信号评分作为兜底。"""
        if trend_result is None:
            return None
        try:
            score = int(getattr(trend_result, "signal_score", 0))
        except (TypeError, ValueError):
            return None
        return score if score > 0 else None

    @staticmethod
    def _trend_label_fallback(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str = "zh",
    ) -> str:
        """从技术分析返回本地化的趋势文案，作为 Agent 的兜底取值。"""
        if trend_result is None:
            return ""
        trend_status = getattr(trend_result, "trend_status", None)
        value = getattr(trend_status, "value", None) or str(trend_status or "").strip()
        if report_language != "en":
            return value
        return localize_trend_prediction(value, report_language)

    @staticmethod
    def _trend_signal_fallback(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str = "zh",
    ) -> str:
        """从技术分析的买卖信号返回本地化的操作建议，作为 Agent 兜底。"""
        if trend_result is None:
            return ""
        buy_signal = getattr(trend_result, "buy_signal", None)
        value = getattr(buy_signal, "value", None) or str(buy_signal or "").strip()
        return localize_operation_advice(value, report_language)

    @staticmethod
    def _trend_decision_fallback(trend_result: Optional[TrendAnalysisResult]) -> Optional[str]:
        """将技术分析的买/卖枚举名映射为 dashboard 的决策值（buy/hold/sell）。"""
        if trend_result is None:
            return None
        signal_name = getattr(getattr(trend_result, "buy_signal", None), "name", "").lower()
        return {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }.get(signal_name)

    @staticmethod
    def _mark_trend_fallback_source(result: AnalysisResult) -> None:
        """当字段由趋势逻辑填充时，追加一个数据源标记（data-source marker）。"""
        if "trend:fallback" in (result.data_sources or ""):
            return
        result.data_sources = (
            f"{result.data_sources},trend:fallback"
            if result.data_sources
            else "trend:fallback"
        )

    @staticmethod
    def _summary_fallback_from_result(result: AnalysisResult, report_language: str) -> str:
        """从归一化后的趋势/建议字段拼出一句摘要（单行）。"""
        trend = (result.trend_prediction or "").strip()
        advice = (result.operation_advice or "").strip()
        if trend and advice:
            if report_language == "en":
                return f"Trend view: {trend}; action advice: {advice}."
            return f"趋势结论：{trend}；操作建议：{advice}。"
        return ""

    def _backfill_agent_dashboard_fields(
        self,
        result: AnalysisResult,
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> None:
        """用归一化后的结果值回填（backfill）Agent dashboard 缺失字段。

        Web 前端期望一个相对完整的 dashboard 对象。当 LLM 省略可选字段时，用技术分析的
        兜底值填充，使 API 响应保持可用，同时不冒充这些值来自 Agent。
        """
        if not isinstance(result.dashboard, dict):
            result.dashboard = {}
        dashboard = result.dashboard

        for key in (
            "sentiment_score",
            "trend_prediction",
            "operation_advice",
            "decision_type",
            "confidence_level",
            "analysis_summary",
        ):
            current = dashboard.get(key)
            if key == "sentiment_score":
                if self._is_agent_field_missing(current, scalar=True):
                    dashboard[key] = getattr(result, key)
            elif self._is_agent_field_missing(current, scalar=True, expect_text=True):
                dashboard[key] = getattr(result, key)

        core = dashboard.get("core_conclusion")
        if not isinstance(core, dict):
            core = {}
            dashboard["core_conclusion"] = core
        if self._is_agent_field_missing(core.get("one_sentence"), scalar=True):
            core["one_sentence"] = result.analysis_summary or self._summary_fallback_from_result(
                result,
                report_language,
            ) or ("Analysis pending" if report_language == "en" else "分析待补充")

        intelligence = dashboard.get("intelligence")
        if not isinstance(intelligence, dict):
            intelligence = {}
            dashboard["intelligence"] = intelligence
        risk_alerts = intelligence.get("risk_alerts")
        if (
            "risk_alerts" not in intelligence
            or self._is_agent_field_missing(risk_alerts)
            or not isinstance(risk_alerts, list)
        ):
            risk_factors = getattr(trend_result, "risk_factors", None) or []
            intelligence["risk_alerts"] = list(risk_factors)

        if result.decision_type in ("buy", "hold"):
            battle = dashboard.get("battle_plan")
            if not isinstance(battle, dict):
                battle = {}
                dashboard["battle_plan"] = battle
            sniper_points = battle.get("sniper_points")
            if not isinstance(sniper_points, dict):
                sniper_points = {}
                battle["sniper_points"] = sniper_points
            if self._is_agent_field_missing(sniper_points.get("stop_loss"), scalar=True):
                sniper_points["stop_loss"] = self._stop_loss_fallback_from_trend(
                    trend_result,
                    report_language,
                )

    @staticmethod
    def _stop_loss_fallback_from_trend(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> Any:
        """用最近的技术支撑位作为止损提示的兜底取值。"""
        levels = getattr(trend_result, "support_levels", None) if trend_result else None
        if levels:
            return levels[0]
        return "To be completed" if report_language == "en" else "待补充"

    @staticmethod
    def _apply_trend_fallback(
        result: AnalysisResult,
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> None:
        """当 Agent 输出不可用时，填充 ``AnalysisResult`` 的核心字段（用趋势兜底）。"""
        if trend_result is None:
            result.sentiment_score = 50
            result.operation_advice = "Watch" if report_language == "en" else "观望"
            return

        score = getattr(trend_result, "signal_score", None)
        try:
            numeric_score = int(score)
        except (TypeError, ValueError):
            numeric_score = 50
        result.sentiment_score = numeric_score if numeric_score > 0 else 50

        trend_label = StockAnalysisPipeline._trend_label_fallback(trend_result, report_language)
        if trend_label:
            result.trend_prediction = trend_label

        buy_signal = getattr(trend_result, "buy_signal", None)
        signal_label = StockAnalysisPipeline._trend_signal_fallback(
            trend_result,
            report_language,
        )
        if signal_label:
            result.operation_advice = signal_label
        else:
            result.operation_advice = "Watch" if report_language == "en" else "观望"

        from src.agent.protocols import normalize_decision_signal

        signal_name = getattr(buy_signal, "name", "").lower()
        signal_to_decision = {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }
        result.decision_type = signal_to_decision.get(signal_name, result.decision_type or "hold")
        result.decision_type = normalize_decision_signal(result.decision_type)
        result.data_sources = f"{result.data_sources},trend:fallback" if result.data_sources else "trend:fallback"

    @staticmethod
    def _is_placeholder_stock_name(name: str, code: str) -> bool:
        """当股票名为空或类似占位符时返回 True。"""
        if not name:
            return True
        normalized = str(name).strip()
        if not normalized:
            return True
        if normalized == code:
            return True
        if normalized.startswith("股票"):
            return True
        if "Unknown" in normalized:
            return True
        return False

    @staticmethod
    def _safe_int(value: Any, default: int = 50) -> int:
        """安全地将值转换为整数。"""
        if value is None:
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            import re
            match = re.search(r'-?\d+', value)
            if match:
                return int(match.group())
        return default
    
    def _describe_volume_ratio(self, volume_ratio: float) -> str:
        """
        量比描述
        
        量比 = 当前成交量 / 过去5日平均成交量
        """
        if volume_ratio < 0.5:
            return "极度萎缩"
        elif volume_ratio < 0.8:
            return "明显萎缩"
        elif volume_ratio < 1.2:
            return "正常"
        elif volume_ratio < 2.0:
            return "温和放量"
        elif volume_ratio < 3.0:
            return "明显放量"
        else:
            return "巨量"

    @staticmethod
    def _compute_ma_status(close: float, ma5: float, ma10: float, ma20: float) -> str:
        """
        根据价格与各均线值计算均线多空排列状态。

        逻辑与 storage._analyze_ma_status 保持一致（Issue #234）。
        """
        close = close or 0
        ma5 = ma5 or 0
        ma10 = ma10 or 0
        ma20 = ma20 or 0
        if close > ma5 > ma10 > ma20 > 0:
            return "多头排列 📈"
        elif close < ma5 < ma10 < ma20 and ma20 > 0:
            return "空头排列 📉"
        elif close > ma5 and ma5 > ma10:
            return "短期向好 🔼"
        elif close < ma5 and ma5 < ma10:
            return "短期走弱 🔽"
        else:
            return "震荡整理 ↔️"

    def _augment_historical_with_realtime(
        self, df: pd.DataFrame, realtime_quote: Any, code: str
    ) -> pd.DataFrame:
        """
        用当日实时行情补齐历史 OHLCV，供盘中均线（MA）计算使用。

        Issue #234：技术指标应基于实时价而非昨收，否则盘中分析会沿用过期口径。
        非交易日或开关关闭时直接返回原 DataFrame（fail-open）。
        """
        if df is None or df.empty or 'close' not in df.columns:
            return df
        if realtime_quote is None:
            return df
        price = getattr(realtime_quote, 'price', None)
        if price is None or not (isinstance(price, (int, float)) and price > 0):
            return df

        # 可选开关：非交易日/关闭实时技术指标时跳过补齐（fail-open 返回原数据）
        enable_realtime_tech = getattr(
            self.config, 'enable_realtime_technical_indicators', True
        )
        if not enable_realtime_tech:
            return df
        market = get_market_for_stock(code)
        market_today = get_market_now(market).date()
        if market and not is_market_open(market, market_today):
            return df

        last_val = df['date'].max()
        last_date = (
            last_val.date() if hasattr(last_val, 'date') else
            (last_val if isinstance(last_val, date) else pd.Timestamp(last_val).date())
        )
        yesterday_close = float(df.iloc[-1]['close']) if len(df) > 0 else price
        open_p = getattr(realtime_quote, 'open_price', None) or getattr(
            realtime_quote, 'pre_close', None
        ) or yesterday_close
        high_p = getattr(realtime_quote, 'high', None) or price
        low_p = getattr(realtime_quote, 'low', None) or price
        vol = getattr(realtime_quote, 'volume', None) or 0
        amt = getattr(realtime_quote, 'amount', None)
        pct = getattr(realtime_quote, 'change_pct', None)

        if last_date >= market_today:
            # 当日已有 Bar：就地更新最后一行为实时 OHLC（先 copy，避免改动调用方的 df）
            df = df.copy()
            idx = df.index[-1]
            df.loc[idx, 'close'] = price
            if open_p is not None:
                df.loc[idx, 'open'] = open_p
            if high_p is not None:
                df.loc[idx, 'high'] = high_p
            if low_p is not None:
                df.loc[idx, 'low'] = low_p
            if vol:
                df.loc[idx, 'volume'] = vol
            if amt is not None:
                df.loc[idx, 'amount'] = amt
            if pct is not None:
                df.loc[idx, 'pct_chg'] = pct
        else:
            # 当日尚无 Bar：追加一根由实时行情构造的"虚拟当日"行，使均线能纳入今天
            new_row = {
                'code': code,
                'date': market_today,
                'open': open_p,
                'high': high_p,
                'low': low_p,
                'close': price,
                'volume': vol,
                'amount': amt if amt is not None else 0,
                'pct_chg': pct if pct is not None else 0,
            }
            new_df = pd.DataFrame([new_row])
            df = pd.concat([df, new_df], ignore_index=True)
        return df

    def _build_context_snapshot(
        self,
        enhanced_context: Dict[str, Any],
        news_content: Optional[str],
        realtime_quote: Any,
        chip_data: Optional[ChipDistribution]
    ) -> Dict[str, Any]:
        """
        构建落库的上下文快照。

        快照前会剔除 ``portfolio_context``：持仓属于用户隐私数据，不应随分析记录持久化。
        """
        persisted_context = dict(enhanced_context)
        persisted_context.pop("portfolio_context", None)
        snapshot = {
            "enhanced_context": persisted_context,
            "news_content": news_content,
            "realtime_quote_raw": self._safe_to_dict(realtime_quote),
            "chip_distribution_raw": self._safe_to_dict(chip_data),
        }
        if MARKET_PHASE_SUMMARY_KEY in persisted_context:
            snapshot[MARKET_PHASE_SUMMARY_KEY] = persisted_context[MARKET_PHASE_SUMMARY_KEY]
        if self.analysis_skills is not None:
            snapshot["skills"] = list(self.analysis_skills)
        return snapshot

    @staticmethod
    def _resolve_resume_target_date(
        code: str, current_time: Optional[datetime] = None
    ) -> date:
        """
        解析断点续传（checkpoint/resume）检查所依据的交易日。

        复用交易日历把"今天"折算成最新可复用的交易日，避免休市日反复触发网络抓取。
        """
        market = get_market_for_stock(normalize_stock_code(code))
        return get_effective_trading_date(market, current_time=current_time)

    @staticmethod
    def _safe_to_dict(value: Any) -> Optional[Dict[str, Any]]:
        """
        安全转换为字典
        """
        if value is None:
            return None
        if hasattr(value, "to_dict"):
            try:
                return value.to_dict()
            except Exception:
                return None
        if hasattr(value, "__dict__"):
            try:
                return dict(value.__dict__)
            except Exception:
                return None
        return None

    def _resolve_query_source(self, query_source: Optional[str]) -> str:
        """
        解析请求来源。

        优先级（从高到低）：
        1. 显式传入的 query_source：调用方明确指定时优先使用，便于覆盖推断结果或兼容未来 source_message 来自非 bot 的场景
        2. 存在 source_message 时推断为 "bot"：当前约定为机器人会话上下文
        3. 存在 query_id 时推断为 "web"：Web 触发的请求会带上 query_id
        4. 默认 "system"：定时任务或 CLI 等无上述上下文时

        Args:
            query_source: 调用方显式指定的来源，如 "bot" / "web" / "cli" / "system"

        Returns:
            归一化后的来源标识字符串，如 "bot" / "web" / "cli" / "system"
        """
        if query_source:
            return query_source
        if self.source_message:
            return "bot"
        if self.query_id:
            return "web"
        return "system"

    def _build_query_context(self, query_id: Optional[str] = None) -> Dict[str, str]:
        """
        生成用户查询关联信息
        """
        effective_query_id = query_id or self.query_id or ""

        context: Dict[str, str] = {
            "query_id": effective_query_id,
            "query_source": self.query_source or "",
        }

        if self.source_message:
            context.update({
                "requester_platform": self.source_message.platform or "",
                "requester_user_id": self.source_message.user_id or "",
                "requester_user_name": self.source_message.user_name or "",
                "requester_chat_id": self.source_message.chat_id or "",
                "requester_message_id": self.source_message.message_id or "",
                "requester_query": self.source_message.content or "",
            })

        return context
    
    def process_single_stock(
        self,
        code: str,
        skip_analysis: bool = False,
        single_stock_notify: bool = False,
        report_type: ReportType = ReportType.SIMPLE,
        analysis_query_id: Optional[str] = None,
        current_time: Optional[datetime] = None,
    ) -> Optional[AnalysisResult]:
        """
        处理单只股票的完整流程

        包括：
        1. 获取数据
        2. 保存数据
        3. AI 分析
        4. 单股推送（可选，#55）

        此方法会被线程池调用，需要处理好异常

        Args:
            analysis_query_id: 查询链路关联 id
            code: 股票代码
            skip_analysis: 是否跳过 AI 分析
            single_stock_notify: 是否启用单股推送模式（每分析完一只立即推送）
            report_type: 报告类型枚举（从配置读取，Issue #119）
            current_time: 本轮运行冻结的参考时间，用于统一断点续传目标交易日判断

        Returns:
            AnalysisResult 或 None
        """
        logger.info(f"========== 开始处理 {code} ==========")

        from src.services.history_loader import set_frozen_target_date, reset_frozen_target_date
        frozen_td = self._resolve_resume_target_date(code, current_time=current_time)
        token = set_frozen_target_date(frozen_td)
        effective_query_id = analysis_query_id or self.query_id or uuid.uuid4().hex
        diagnostic_token = None
        if get_current_diagnostic_context() is None:
            diagnostic_token = activate_run_diagnostic_context(
                trace_id=self.trace_id or effective_query_id,
                query_id=effective_query_id,
                stock_code=code,
                trigger_source=self.query_source,
            )
        try:
            self._emit_progress(12, f"{code}：正在准备分析任务")
            # Step 1: 获取并保存数据
            success, error = self.fetch_and_save_stock_data(
                code, current_time=current_time
            )
            
            if not success:
                logger.warning(f"[{code}] 数据获取失败: {error}")
                # 即使获取失败，也尝试用已有数据分析
            else:
                self._emit_progress(16, f"{code}：行情数据准备完成")
            
            # Step 2: AI 分析
            if skip_analysis:
                logger.info(f"[{code}] 跳过 AI 分析（dry-run 模式）")
                return None
            
            result = self.analyze_stock(code, report_type, query_id=effective_query_id)
            
            if result and result.success:
                logger.info(
                    f"[{code}] 分析完成: {result.operation_advice}, "
                    f"评分 {result.sentiment_score}"
                )
                
                # 单股推送模式（#55）：每分析完一只股票立即推送
                if single_stock_notify:
                    self._send_single_stock_notification(
                        result,
                        report_type=report_type,
                        fallback_code=code,
                    )
            elif result:
                logger.warning(
                    f"[{code}] 分析未成功: {result.error_message or '未知错误'}"
                )
            
            return result
            
        except Exception as e:
            # 捕获所有异常，确保单股失败不影响整体
            logger.exception(f"[{code}] 处理过程发生未知异常: {e}")
            return None
        finally:
            reset_run_diagnostic_context(diagnostic_token)
            reset_frozen_target_date(token)
    
    def run(
        self,
        stock_codes: Optional[List[str]] = None,
        dry_run: bool = False,
        send_notification: bool = True,
        merge_notification: bool = False
    ) -> List[AnalysisResult]:
        """
        运行完整的分析流程

        流程：
        1. 获取待分析的股票列表
        2. 使用线程池并发处理
        3. 收集分析结果
        4. 发送通知

        Args:
            stock_codes: 股票代码列表（可选，默认使用配置中的自选股）
            dry_run: 是否仅获取数据不分析
            send_notification: 是否发送推送通知
            merge_notification: 是否合并推送（跳过本次推送，由 main 层合并个股+大盘后统一发送，Issue #190）

        Returns:
            分析结果列表
        """
        start_time = time.time()
        
        # 使用配置中的股票列表
        if stock_codes is None:
            self.config.refresh_stock_list()
            stock_codes = self.config.stock_list
        
        if not stock_codes:
            logger.error("未配置自选股列表，请在 .env 文件中设置 STOCK_LIST")
            return []
        
        logger.info(f"===== 开始分析 {len(stock_codes)} 只股票 =====")
        logger.info(f"股票列表: {', '.join(stock_codes)}")
        logger.info(f"并发数: {self.max_workers}, 模式: {'仅获取数据' if dry_run else '完整分析'}")

        # 冻结本轮运行的统一参考时间，避免跨市场收盘边界时同批股票使用不同目标交易日。
        resume_reference_time = datetime.now(timezone.utc)
        
        # === 批量预取实时行情（优化：避免每只股票都触发全量拉取）===
        # 只有股票数量 >= 5 时才进行预取，少量股票直接逐个查询更高效
        if len(stock_codes) >= 5:
            prefetch_count = self.fetcher_manager.prefetch_realtime_quotes(stock_codes)
            if prefetch_count > 0:
                logger.info(f"已启用批量预取架构：一次拉取全市场数据，{len(stock_codes)} 只股票共享缓存")

        # Issue #455: 预取股票名称，避免并发分析时显示「股票xxxxx」
        # dry_run 仅做数据拉取，不需要名称预取，避免额外网络开销
        if not dry_run:
            self.fetcher_manager.prefetch_stock_names(stock_codes, use_bulk=False)

        # 单股推送模式（#55）：从配置读取
        single_stock_notify = getattr(self.config, 'single_stock_notify', False)
        # Issue #119: 从配置读取报告类型
        report_type_str = getattr(self.config, 'report_type', 'simple').lower()
        if report_type_str == 'brief':
            report_type = ReportType.BRIEF
        elif report_type_str == 'full':
            report_type = ReportType.FULL
        else:
            report_type = ReportType.SIMPLE
        # Issue #128: 从配置读取分析间隔
        analysis_delay = getattr(self.config, 'analysis_delay', 0)

        if single_stock_notify:
            logger.info(
                "已启用单股推送模式：分析仍并发执行，通知改为在结果收集侧串行发送（报告类型: %s）",
                report_type_str,
            )
        
        results: List[AnalysisResult] = []
        
        # 使用线程池并发处理
        # 注意：max_workers 设置较低（默认3）以避免触发反爬
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交任务
            future_to_code = {
                executor.submit(
                    self.process_single_stock,
                    code,
                    skip_analysis=dry_run,
                    single_stock_notify=False,
                    report_type=report_type,  # Issue #119: 传递报告类型
                    analysis_query_id=uuid.uuid4().hex,
                    current_time=resume_reference_time,
                ): code
                for code in stock_codes
            }
            
            # 收集结果
            for idx, future in enumerate(as_completed(future_to_code)):
                code = future_to_code[future]
                try:
                    result = future.result()
                    if result and result.success:
                        results.append(result)
                        if single_stock_notify and send_notification and not dry_run:
                            self._send_single_stock_notification(
                                result,
                                report_type=report_type,
                                fallback_code=code,
                            )
                    elif result and not result.success:
                        logger.warning(
                            f"[{code}] 分析结果标记为失败，不计入汇总: "
                            f"{result.error_message or '未知原因'}"
                        )

                    # Issue #128: 分析间隔 - 在个股分析和大盘分析之间添加延迟
                    if idx < len(stock_codes) - 1 and analysis_delay > 0:
                        # 注意：此 sleep 发生在“主线程收集 future 的循环”中，
                        # 并不会阻止线程池中的任务同时发起网络请求。
                        # 因此它对降低并发请求峰值的效果有限；真正的峰值主要由 max_workers 决定。
                        # 该行为目前保留（按需求不改逻辑）。
                        logger.debug(f"等待 {analysis_delay} 秒后继续下一只股票...")
                        time.sleep(analysis_delay)

                except Exception as e:
                    logger.error(f"[{code}] 任务执行失败: {e}")
        
        # 统计
        elapsed_time = time.time() - start_time
        
        # dry-run 模式下，数据获取成功即视为成功
        if dry_run:
            # 检查哪些股票的最新可复用交易日数据已存在
            success_count = sum(
                1
                for code in stock_codes
                if self.db.has_today_data(
                    code,
                    self._resolve_resume_target_date(
                        code, current_time=resume_reference_time
                    ),
                )
            )
            fail_count = len(stock_codes) - success_count
        else:
            success_count = len(results)
            fail_count = len(stock_codes) - success_count
        
        logger.info("===== 分析完成 =====")
        logger.info(f"成功: {success_count}, 失败: {fail_count}, 耗时: {elapsed_time:.2f} 秒")
        
        # 保存报告到本地文件（无论是否推送通知都保存）
        if results and not dry_run:
            self._save_local_report(results, report_type)

        # 发送通知（单股推送模式下跳过汇总推送，避免重复）
        if results and send_notification and not dry_run:
            if single_stock_notify:
                # 单股推送模式：只保存汇总报告，不再重复推送
                logger.info("单股推送模式：跳过汇总推送，仅保存报告到本地")
                self._send_notifications(results, report_type, skip_push=True)
            elif merge_notification:
                # 合并模式（Issue #190）：仅保存，不推送，由 main 层合并个股+大盘后统一发送
                logger.info("合并推送模式：跳过本次推送，将在个股+大盘复盘后统一发送")
                self._send_notifications(results, report_type, skip_push=True)
            else:
                self._send_notifications(results, report_type)
        
        return results

    def _send_single_stock_notification(
        self,
        result: AnalysisResult,
        report_type: ReportType = ReportType.SIMPLE,
        fallback_code: Optional[str] = None,
    ) -> None:
        """发送单股通知，供直接单股入口和批量串行推送共用。"""
        if not self.notifier.is_available():
            return

        stock_code = getattr(result, "code", None) or fallback_code or "unknown"
        notify_lock = getattr(self, "_single_stock_notify_lock", None)
        if notify_lock is None:
            with _SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD:
                notify_lock = getattr(self, "_single_stock_notify_lock", None)
                if notify_lock is None:
                    notify_lock = threading.Lock()
                    setattr(self, "_single_stock_notify_lock", notify_lock)

        with notify_lock:
            try:
                if report_type == ReportType.FULL:
                    report_content = self.notifier.generate_dashboard_report([result])
                    logger.info(f"[{stock_code}] 使用完整报告格式")
                elif report_type == ReportType.BRIEF:
                    report_content = self.notifier.generate_brief_report([result])
                    logger.info(f"[{stock_code}] 使用简洁报告格式")
                else:
                    report_content = self.notifier.generate_single_stock_report(result)
                    logger.info(f"[{stock_code}] 使用精简报告格式")

                if self.notifier.send(
                    report_content,
                    email_stock_codes=[stock_code],
                    route_type="report",
                    severity="info",
                    dedup_key=f"report:single:{stock_code}:{report_type.value}",
                    cooldown_key=f"report:single:{stock_code}:{report_type.value}",
                ):
                    logger.info(f"[{stock_code}] 单股推送成功")
                else:
                    logger.warning(f"[{stock_code}] 单股推送失败")
            except Exception as e:
                logger.error(f"[{stock_code}] 单股推送异常: {e}")

    def _save_local_report(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
    ) -> None:
        """保存分析报告到本地文件（与通知推送解耦）"""
        try:
            report = self._generate_aggregate_report(results, report_type)
            filepath = self.notifier.save_report_to_file(report)
            logger.info(f"决策仪表盘日报已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存本地报告失败: {e}")

    def _send_notifications(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
        skip_push: bool = False,
    ) -> None:
        """
        发送分析结果通知
        
        生成决策仪表盘格式的报告
        
        Args:
            results: 分析结果列表
            skip_push: 是否跳过推送（仅保存到本地，用于单股推送模式）
        """
        noise_decision = None
        noise_finalized = False
        try:
            logger.info("生成决策仪表盘日报...")
            report = self._generate_aggregate_report(results, report_type)
            
            # 跳过推送（单股推送模式 / 合并模式：报告已由 _save_local_report 保存）
            if skip_push:
                return
            
            # 推送通知
            if self.notifier.is_available():
                channels = self.notifier.get_available_channels()
                channels = self.notifier.get_channels_for_route("report", channels=channels)
                context_success = self.notifier.send_to_context(report)
                if channels and hasattr(self.notifier, "evaluate_noise_control"):
                    report_type_key = report_type.value if isinstance(report_type, ReportType) else str(report_type)
                    codes_key = ",".join(
                        sorted(str(getattr(result, "code", "") or "") for result in results)
                    )
                    noise_key = f"report:aggregate:{report_type_key}:{codes_key}"
                    noise_decision = self.notifier.evaluate_noise_control(
                        report,
                        route_type="report",
                        severity="info",
                        dedup_key=noise_key,
                        cooldown_key=noise_key,
                    )
                    if not noise_decision.should_send:
                        logger.info(noise_decision.message)
                        return

                # Issue #455: Markdown 转图片（与 notification.send 逻辑一致）
                from src.md2img import markdown_to_image

                channels_needing_image = {
                    ch for ch in channels
                    if ch.value in self.notifier._markdown_to_image_channels
                    and ch not in {NotificationChannel.NTFY, NotificationChannel.GOTIFY}
                }
                non_wechat_channels_needing_image = {
                    ch for ch in channels_needing_image if ch != NotificationChannel.WECHAT
                }

                def _get_md2img_hint() -> str:
                    """返回当前配置的 Markdown 渲染引擎对应的安装提示。"""
                    try:
                        engine = getattr(get_config(), "md2img_engine", "wkhtmltoimage")
                    except Exception:
                        engine = "wkhtmltoimage"
                    return (
                        "npm i -g markdown-to-file" if engine == "markdown-to-file"
                        else "wkhtmltopdf (apt install wkhtmltopdf / brew install wkhtmltopdf)"
                    )

                def _send_channel_safely(channel_label: str, send_func: Callable[[], bool]) -> bool:
                    """发送单个渠道，并把异常转换为"该渠道失败"的结果。"""
                    try:
                        return bool(send_func())
                    except Exception as e:
                        logger.exception(
                            "通知渠道 %s 推送异常，继续尝试其他渠道: %s",
                            channel_label,
                            e,
                        )
                        return False

                image_bytes = None
                if non_wechat_channels_needing_image:
                    image_bytes = markdown_to_image(
                        report, max_chars=self.notifier._markdown_to_image_max_chars
                    )
                    if image_bytes:
                        logger.info(
                            "Markdown 已转换为图片，将向 %s 发送图片",
                            [ch.value for ch in non_wechat_channels_needing_image],
                        )
                    else:
                        logger.warning(
                            "Markdown 转图片失败，将回退为文本发送。请检查 MARKDOWN_TO_IMAGE_CHANNELS 配置并安装 %s",
                            _get_md2img_hint(),
                        )

                # 企业微信：只发精简版（平台限制）
                wechat_success = False
                if NotificationChannel.WECHAT in channels:
                    def _send_wechat_report() -> bool:
                        """发送企业微信：使用其专属的精简版仪表盘内容。"""
                        if report_type == ReportType.BRIEF:
                            dashboard_content = self.notifier.generate_brief_report(results)
                        else:
                            dashboard_content = self.notifier.generate_wechat_dashboard(results)
                        logger.info(f"企业微信仪表盘长度: {len(dashboard_content)} 字符")
                        logger.debug(f"企业微信推送内容:\n{dashboard_content}")
                        wechat_image_bytes = None
                        if NotificationChannel.WECHAT in channels_needing_image:
                            wechat_image_bytes = markdown_to_image(
                                dashboard_content,
                                max_chars=self.notifier._markdown_to_image_max_chars,
                            )
                            if wechat_image_bytes is None:
                                logger.warning(
                                    "企业微信 Markdown 转图片失败，将回退为文本发送。请检查 MARKDOWN_TO_IMAGE_CHANNELS 配置并安装 %s",
                                    _get_md2img_hint(),
                                )
                        use_image = self.notifier._should_use_image_for_channel(
                            NotificationChannel.WECHAT, wechat_image_bytes
                        )
                        if use_image:
                            return self.notifier._send_wechat_image(wechat_image_bytes)
                        return self.notifier.send_to_wechat(dashboard_content)

                    wechat_success = _send_channel_safely(
                        NotificationChannel.WECHAT.value,
                        _send_wechat_report,
                    )

                # 其他渠道：发完整报告（避免自定义 Webhook 被 wechat 截断逻辑污染）
                non_wechat_success = False
                stock_email_groups = getattr(self.config, 'stock_email_groups', []) or []
                for channel in channels:
                    if channel == NotificationChannel.WECHAT:
                        continue
                    if channel == NotificationChannel.FEISHU:
                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_feishu(report),
                        ) or non_wechat_success
                    elif channel == NotificationChannel.TELEGRAM:
                        def _send_telegram_report() -> bool:
                            """发送 Telegram：配置为图片模式时发图，否则发文本。"""
                            use_image = self.notifier._should_use_image_for_channel(
                                channel, image_bytes
                            )
                            if use_image:
                                return self.notifier._send_telegram_photo(image_bytes)
                            return self.notifier.send_to_telegram(report)

                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            _send_telegram_report,
                        ) or non_wechat_success
                    elif channel == NotificationChannel.EMAIL:
                        if stock_email_groups:
                            code_to_emails: Dict[str, Optional[List[str]]] = {}
                            for r in results:
                                if r.code not in code_to_emails:
                                    canonical = normalize_stock_code(r.code)
                                    emails = []
                                    for stocks, emails_list in stock_email_groups:
                                        if canonical in stocks:
                                            emails.extend(emails_list)
                                    code_to_emails[r.code] = list(dict.fromkeys(emails)) if emails else None
                            emails_to_results: Dict[Optional[Tuple], List] = defaultdict(list)
                            for r in results:
                                recs = code_to_emails.get(r.code)
                                key = tuple(recs) if recs else None
                                emails_to_results[key].append(r)
                            for key, group_results in emails_to_results.items():
                                receivers = list(key) if key is not None else None

                                def _send_email_group(
                                    group_results=group_results,
                                    receivers=receivers,
                                ) -> bool:
                                    """发送某一个按股票分组的邮件收件人组。"""
                                    grp_report = self._generate_aggregate_report(group_results, report_type)
                                    subject = self._build_email_subject(group_results)
                                    grp_image_bytes = None
                                    if channel.value in self.notifier._markdown_to_image_channels:
                                        grp_image_bytes = markdown_to_image(
                                            grp_report,
                                            max_chars=self.notifier._markdown_to_image_max_chars,
                                        )
                                    use_image = self.notifier._should_use_image_for_channel(
                                        channel, grp_image_bytes
                                    )
                                    if use_image:
                                        return self.notifier._send_email_with_inline_image(
                                            grp_image_bytes,
                                            receivers=receivers,
                                            subject=subject,
                                        )
                                    return self.notifier.send_to_email(
                                        grp_report,
                                        receivers=receivers,
                                        subject=subject,
                                    )

                                email_label = (
                                    f"{channel.value}:{','.join(receivers)}"
                                    if receivers else f"{channel.value}:default"
                                )
                                non_wechat_success = _send_channel_safely(
                                    email_label,
                                    _send_email_group,
                                ) or non_wechat_success
                        else:
                            def _send_email_report() -> bool:
                                """发送默认的汇总邮件报告。"""
                                subject = self._build_email_subject(results)
                                use_image = self.notifier._should_use_image_for_channel(
                                    channel, image_bytes
                                )
                                if use_image:
                                    return self.notifier._send_email_with_inline_image(
                                        image_bytes,
                                        subject=subject,
                                    )
                                return self.notifier.send_to_email(report, subject=subject)

                            non_wechat_success = _send_channel_safely(
                                channel.value,
                                _send_email_report,
                            ) or non_wechat_success
                    elif channel == NotificationChannel.CUSTOM:
                        def _send_custom_report() -> bool:
                            """发送自定义 Webhook 内容，支持图片发送并在失败时回退为文本。"""
                            use_image = self.notifier._should_use_image_for_channel(
                                channel, image_bytes
                            )
                            if use_image:
                                return self.notifier._send_custom_webhook_image(
                                    image_bytes, fallback_content=report
                                )
                            return self.notifier.send_to_custom(report)

                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            _send_custom_report,
                        ) or non_wechat_success
                    elif channel == NotificationChannel.PUSHPLUS:
                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_pushplus(report),
                        ) or non_wechat_success
                    elif channel == NotificationChannel.SERVERCHAN3:
                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_serverchan3(report),
                        ) or non_wechat_success
                    elif channel == NotificationChannel.DISCORD:
                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_discord(report),
                        ) or non_wechat_success
                    elif channel == NotificationChannel.PUSHOVER:
                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_pushover(report),
                        ) or non_wechat_success
                    elif channel == NotificationChannel.NTFY:
                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_ntfy(report),
                        ) or non_wechat_success
                    elif channel == NotificationChannel.GOTIFY:
                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_gotify(report),
                        ) or non_wechat_success
                    elif channel == NotificationChannel.ASTRBOT:
                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_astrbot(report),
                        ) or non_wechat_success
                    elif channel == NotificationChannel.SLACK:
                        def _send_slack_report() -> bool:
                            """发送 Slack：凭据完整时上传图片，否则发送文本消息。"""
                            use_image = self.notifier._should_use_image_for_channel(
                                channel, image_bytes
                            )
                            if use_image and self.notifier._slack_bot_token and self.notifier._slack_channel_id:
                                return self.notifier._send_slack_image(
                                    image_bytes, fallback_content=report
                                )
                            return self.notifier.send_to_slack(report)

                        non_wechat_success = _send_channel_safely(
                            channel.value,
                            _send_slack_report,
                        ) or non_wechat_success
                    else:
                        logger.warning(f"未知通知渠道: {channel}")

                success = wechat_success or non_wechat_success or context_success
                if (
                    (wechat_success or non_wechat_success)
                    and noise_decision is not None
                    and hasattr(self.notifier, "record_noise_control")
                ):
                    self.notifier.record_noise_control(noise_decision)
                    noise_finalized = True
                elif (
                    noise_decision is not None
                    and hasattr(self.notifier, "release_noise_control")
                ):
                    self.notifier.release_noise_control(noise_decision)
                    noise_finalized = True
                if success:
                    logger.info("决策仪表盘推送成功")
                else:
                    logger.warning("决策仪表盘推送失败")
            else:
                logger.info("通知渠道未配置，跳过推送")
                
        except Exception as e:
            if (
                noise_decision is not None
                and not noise_finalized
                and hasattr(self.notifier, "release_noise_control")
            ):
                self.notifier.release_noise_control(noise_decision)
            import traceback
            logger.error(f"发送通知失败: {e}\n{traceback.format_exc()}")

    @staticmethod
    def _build_email_subject(results: List[AnalysisResult]) -> str:
        """构建简洁的邮件标题；仅单只股票时附带股票名称与代码。"""
        date_str = datetime.now().strftime('%Y-%m-%d')
        if len(results) == 1:
            result = results[0]
            code = str(getattr(result, "code", "") or "").strip()
            name = str(getattr(result, "name", "") or "").strip()
            stock_label = name or code
            if stock_label and code and code not in stock_label:
                stock_label = f"{stock_label}({code})"
            if stock_label:
                return f"📈 股票智能分析报告 - {stock_label} - {date_str}"
        return f"📈 股票智能分析报告 - {date_str}"

    def _generate_aggregate_report(
        self,
        results: List[AnalysisResult],
        report_type: ReportType,
    ) -> str:
        """生成汇总报告，并保持对旧版 notifier 的向后兼容。"""
        generator = getattr(self.notifier, "generate_aggregate_report", None)
        if callable(generator):
            return generator(results, report_type)
        if report_type == ReportType.BRIEF and hasattr(self.notifier, "generate_brief_report"):
            return self.notifier.generate_brief_report(results)
        return self.notifier.generate_dashboard_report(results)
