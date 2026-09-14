# -*- coding: utf-8 -*-
"""大盘复盘编排模块（覆盖 A股、港股、美股每日复盘）。

本模块是 ``MarketAnalyzer`` 面向 CLI/API/Bot 的封装层。它负责归一化区域选择，
将生成的复盘写入与个股分析相同的历史表，保存 markdown 文件，并可选择性推送通知。
数据采集与提示词（prompt）构造仍保留在 ``MarketAnalyzer`` 中。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
import uuid

from src.config import get_config
from src.notification import NotificationService
from src.market_analyzer import MarketAnalyzer
from src.report_language import normalize_report_language
from src.search_service import SearchService
from src.analyzer import AnalysisResult, GeminiAnalyzer


# 模块级日志记录器，用于输出大盘复盘过程中的诊断信息
logger = logging.getLogger(__name__)

# 复写进历史表的固定代码与报告类型，用于把大盘复盘与个股分析区分开。
# MARKET_REVIEW_HISTORY_CODE: 写入数据库 history 表时使用的 code 字段值，标识这是大盘复盘记录
MARKET_REVIEW_HISTORY_CODE = "MARKET"
# MARKET_REVIEW_REPORT_TYPE: 报告类型标识，用于前端区分展示和数据库分类查询
MARKET_REVIEW_REPORT_TYPE = "market_review"


@dataclass
class MarketReviewRunResult:
    """为 API 调用方保留的结构化结果，同时维持 Markdown 兼容。"""

    # 生成的复盘报告文本（Markdown 格式）
    report: str
    # 结构化的复盘数据负载，供前端渲染或后续程序消费
    market_review_payload: Dict[str, Any] = field(default_factory=dict)


def _coerce_market_review_payload(review_result: Any, *, region: str, report: str) -> Dict[str, Any]:
    """取出结构化 payload；缺失时用单 section 的兼容结构兜底。

    当 MarketAnalyzer 返回的结果包含结构化数据时，直接提取使用；
    否则构造一个最小兼容结构，确保下游消费者始终能拿到统一格式的字典。
    """
    payload = getattr(review_result, "structured_payload", None)
    if isinstance(payload, dict) and payload:
        return payload
    return {
        "version": 1,
        "kind": MARKET_REVIEW_REPORT_TYPE,
        "region": region,
        "title": "",
        "sections": [{"key": "full_review", "title": "Review", "markdown": report or ""}],
        "markdown_report": report or "",
    }


def _build_market_review_payload(
    *, review_report: str, payloads: Dict[str, Dict[str, Any]], region: str, language: str
) -> Dict[str, Any]:
    """按市场数组装复盘 payload：单市场内联其详情，多市场按 markets 分桶。

    当只分析单个市场时，将该市场的结构化数据直接提升为顶层字段；
    当分析多个市场时，将各市场的数据放入 markets 字典中，便于前端按市场切换展示。
    """
    if len(payloads) == 1:
        payload = dict(next(iter(payloads.values())))
        payload.update({
            "version": payload.get("version") or 1,
            "kind": MARKET_REVIEW_REPORT_TYPE,
            "region": region,
            "language": payload.get("language") or normalize_report_language(language),
            "markdown_report": review_report,
        })
        return payload
    return {
        "version": 1,
        "kind": MARKET_REVIEW_REPORT_TYPE,
        "region": region,
        "language": normalize_report_language(language),
        "markets": payloads,
        "markdown_report": review_report,
    }
# （市场键, 标题文案键, 中文名）三元组，数组顺序即多市场报告的拼接顺序。
# 定义了支持的市场列表及其在报告中的展示顺序：A股 -> 港股 -> 美股 -> 日股 -> 韩股
_MARKET_REVIEW_MARKETS = [('cn', 'cn_title', 'A股'), ('hk', 'hk_title', '港股'), ('us', 'us_title', '美股'), ('jp', 'jp_title', '日股'), ('kr', 'kr_title', '韩股')]


def _run_daily_review_with_snapshot(
    market_analyzer: MarketAnalyzer,
) -> Tuple[str, Optional[Dict[str, Any]], Any]:
    """返回一份复盘报告及其同视角的 Market Light 快照。

    该兜底逻辑用于兼容仅暴露历史 ``run_daily_review`` 方法的注入式分析器。
    如果分析器支持 ``run_daily_review_with_snapshot`` 方法，则调用该方法获取报告和快照；
    否则退回到传统的 ``run_daily_review`` 方法，快照返回 None。
    """
    runner = type(market_analyzer).__dict__.get("run_daily_review_with_snapshot")
    if callable(runner):
        result = runner(market_analyzer)
        if hasattr(result, "report"):
            snapshot = getattr(result, "market_light_snapshot", None)
            return result.report, snapshot if isinstance(snapshot, dict) and snapshot else None, result
        report, snapshot = result
        return report, snapshot if isinstance(snapshot, dict) and snapshot else None, result
    report = market_analyzer.run_daily_review()
    return report, None, None


def _get_market_review_text(language: str) -> dict[str, str]:
    """返回本地化标题，用于文件输出、推送文案与各市场分节标题。

    根据语言参数返回对应的中英文标题字典，确保报告和推送的文案与配置的语言一致。
    """
    normalized = normalize_report_language(language)
    if normalized == "en":
        return {
            "root_title": "# 🎯 Market Review",
            "push_title": "🎯 Market Review",
            "cn_title": "# A-share Market Recap",
            "us_title": "# US Market Recap",
            "hk_title": "# HK Market Recap",
            "separator": "> Next market recap follows",
        }
    return {
        "root_title": "# 🎯 大盘复盘",
        "push_title": "🎯 大盘复盘",
        "cn_title": "# A股大盘复盘",
        "us_title": "# 美股大盘复盘",
        "hk_title": "# 港股大盘复盘",
        "separator": "> 以下为下一市场大盘复盘",
    }


def run_market_review(
    notifier: NotificationService,
    analyzer: Optional[GeminiAnalyzer] = None,
    search_service: Optional[SearchService] = None,
    config: Optional[Any] = None,
    send_notification: bool = True,
    merge_notification: bool = False,
    override_region: Optional[str] = None,
    query_id: Optional[str] = None,
    return_structured: bool = False,
    save_report_file: bool = True,
    persist_history: bool = True,
) -> Optional[str] | Optional[MarketReviewRunResult]:
    """执行大盘复盘分析。

    ``merge_notification`` 供个股主分析流程使用：此时大盘复盘仍需生成并落库，
    但推送会延后，以便个股与大盘内容合并后一次性发出。

    Args:
        notifier: 通知服务实例，用于保存报告文件和发送推送
        analyzer: AI分析器（可选），用于增强复盘分析
        search_service: 搜索服务（可选），用于获取实时新闻和补充信息
        send_notification: 是否发送通知推送
        merge_notification: 是否合并推送（跳过本次推送，由 main 层合并个股+大盘后统一发送，Issue #190）
        override_region: 覆盖 config 的 market_review_region（Issue #373 交易日过滤后有效子集）
        query_id: 历史记录关联 ID；API 后台任务会传入 task_id，CLI/Bot 为空时自动生成

    Returns:
        复盘报告文本，或当 return_structured=True 时返回 MarketReviewRunResult 结构化结果
    """
    logger.info("开始执行大盘复盘分析...")
    config = config or get_config()
    # 根据配置的报告语言获取对应的本地化文案
    review_text = _get_market_review_text(getattr(config, "report_language", "zh"))
    # 确定要执行复盘的市场区域：优先使用 override_region，否则从配置读取，默认 A股
    region = (
        override_region
        if override_region is not None
        else (getattr(config, 'market_review_region', 'cn') or 'cn')
    )
    # 定义支持的市场列表及其标题映射（用于多市场报告拼接）
    _ALL_MARKETS = [('cn', 'cn_title', 'A 股'), ('hk', 'hk_title', '港股'), ('us', 'us_title', '美股')]
    # 有效的单市场标识集合
    _VALID_SINGLES = {'cn', 'us', 'hk'}

    # 同时兼容历史遗留的 "both" 标记与新式的逗号分隔子集（如 "cn,us"）。
    # 非法片段直接忽略，避免单个错误 token 导致整个复盘被跳过。
    if ',' in region:
        run_markets = [m.strip() for m in region.split(',') if m.strip() in _VALID_SINGLES]
    elif region == 'both':
        run_markets = list(_VALID_SINGLES)
    elif region in _VALID_SINGLES:
        run_markets = [region]
    else:
        run_markets = ['cn']

    try:
        if len(run_markets) > 1:
            # 多市场顺序执行，合并报告
            # parts: 各市场报告片段列表，用于最终拼接
            parts = []
            # market_light_snapshots: 各市场的 Market Light 快照数据
            market_light_snapshots: Dict[str, Dict[str, Any]] = {}
            # market_review_payloads: 各市场的结构化复盘数据
            market_review_payloads: Dict[str, Dict[str, Any]] = {}
            for mkt, title_key, label in _ALL_MARKETS:
                if mkt not in run_markets:
                    continue
                logger.info("生成 %s 大盘复盘报告...", label)
                # 为每个市场创建独立的 MarketAnalyzer 实例
                mkt_analyzer = MarketAnalyzer(
                    search_service=search_service, analyzer=analyzer, region=mkt
                )
                mkt_report, market_light_snapshot, review_result = _run_daily_review_with_snapshot(mkt_analyzer)
                if market_light_snapshot is not None:
                    market_light_snapshots[mkt] = market_light_snapshot
                market_review_payloads[mkt] = _coerce_market_review_payload(
                    review_result, region=mkt, report=mkt_report
                )
                if mkt_report:
                    parts.append(f"{review_text[title_key]}\n\n{mkt_report}")
            if parts:
                # 使用分隔符将多市场报告拼接为完整报告
                review_report = f"\n\n---\n\n{review_text['separator']}\n\n".join(parts)
            else:
                review_report = None
        else:
            # 单市场场景：直接创建对应区域的 MarketAnalyzer 并执行复盘
            market_analyzer = MarketAnalyzer(
                search_service=search_service,
                analyzer=analyzer,
                region=region,
            )
            review_report, market_light_snapshot, review_result = _run_daily_review_with_snapshot(market_analyzer)
            market_light_snapshots = (
                {region: market_light_snapshot}
                if market_light_snapshot is not None
                else {}
            )
            market_review_payloads = {
                region: _coerce_market_review_payload(review_result, region=region, report=review_report)
            }

        if review_report:
            # 组装最终的结构化复盘数据
            market_review_payload = _build_market_review_payload(
                review_report=review_report,
                payloads=market_review_payloads,
                region=','.join(run_markets),
                language=getattr(config, "report_language", "zh"),
            )
            # 保存报告到文件
            date_str = datetime.now().strftime('%Y%m%d')
            report_filename = f"market_review_{date_str}.md"
            filepath = None
            if save_report_file:
                filepath = notifier.save_report_to_file(
                    f"{review_text['root_title']}\n\n{review_report}",
                    report_filename
                )
            logger.info(f"大盘复盘报告已保存: {filepath}")

            if persist_history:
                # 将复盘结果持久化到分析历史表
                _persist_market_review_history(
                    review_report=review_report,
                    markdown_report=f"{review_text['root_title']}\n\n{review_report}",
                    region=region,
                    config=config,
                    query_id=query_id,
                    market_light_snapshots=market_light_snapshots,
                    market_review_payload=market_review_payload,
                )

            # 推送通知（合并模式下跳过，由 main 层统一发送）
            if merge_notification and send_notification:
                logger.info("合并推送模式：跳过大盘复盘单独推送，将在个股+大盘复盘后统一发送")
            elif send_notification and notifier.is_available():
                # 添加标题
                report_content = f"{review_text['push_title']}\n\n{review_report}"

                success = notifier.send(report_content, email_send_to_all=True, route_type="report")
                if success:
                    logger.info("大盘复盘推送成功")
                else:
                    logger.warning("大盘复盘推送失败")
            elif not send_notification:
                logger.info("已跳过推送通知 (--no-notify)")

            # 根据调用方需求返回文本或结构化结果
            if return_structured:
                return MarketReviewRunResult(
                    report=review_report,
                    market_review_payload=market_review_payload,
                )
            return review_report

    except Exception as e:
        logger.error(f"大盘复盘分析失败: {e}")

    return None


def _persist_market_review_history(
    *,
    review_report: str,
    markdown_report: str,
    region: str,
    config: object,
    query_id: Optional[str] = None,
    market_light_snapshots: Optional[Dict[str, Dict[str, Any]]] = None,
    market_review_payload: Optional[Dict[str, Any]] = None,
) -> int:
    """将大盘复盘输出持久化到既有的分析历史表（AnalysisResult）。

    复盘复用 ``AnalysisResult`` 结构，使 API/历史消费者无需另建存储契约。持久化采用
    尽力而为（best-effort）策略：失败时仅记录日志，绝不能阻断报告文件生成或通知发送。
    """
    try:
        from src.storage import DatabaseManager

        # 统一报告语言为标准化值
        report_language = normalize_report_language(getattr(config, "report_language", "zh"))
        # 从报告中提取摘要，用于历史列表展示
        summary = _summarize_market_review(review_report, report_language)
        if report_language == "en":
            stock_name = "Market Review"
            operation_advice = "View review"
            trend_prediction = "Market review"
        else:
            stock_name = "大盘复盘"
            operation_advice = "查看复盘"
            trend_prediction = "大盘复盘"

        # 构造 AnalysisResult 对象，复用个股分析的历史表结构
        result = AnalysisResult(
            code=MARKET_REVIEW_HISTORY_CODE,
            name=stock_name,
            # 复盘本身没有情绪分，用中性值 50 占位，避免前端出现"看空/看多"误读
            sentiment_score=50,
            trend_prediction=trend_prediction,
            operation_advice=operation_advice,
            analysis_summary=summary,
            report_language=report_language,
            news_summary=review_report,
            raw_response=markdown_report,
            data_sources="market_review",
        )

        # 生成或复用 query_id，用于关联历史记录
        history_query_id = query_id or f"market_review_{uuid.uuid4().hex}"
        # 构建上下文快照，包含报告类型、市场区域等元数据
        context_snapshot = {
            "report_kind": MARKET_REVIEW_REPORT_TYPE,
            "market_review_region": region,
            "report_language": report_language,
        }
        if market_light_snapshots:
            context_snapshot["market_light_snapshots"] = market_light_snapshots
        if market_review_payload:
            context_snapshot["market_review_payload"] = market_review_payload

        # 调用数据库管理器保存分析历史
        saved = DatabaseManager.get_instance().save_analysis_history(
            result=result,
            query_id=history_query_id,
            report_type=MARKET_REVIEW_REPORT_TYPE,
            news_content=review_report,
            context_snapshot=context_snapshot,
            save_snapshot=True,
        )
        if saved:
            logger.info("大盘复盘历史记录已保存: query_id=%s", history_query_id)
        else:
            logger.warning("大盘复盘历史记录保存失败: query_id=%s", history_query_id)
        return saved
    except Exception as exc:
        logger.warning("大盘复盘历史记录保存异常，报告文件与推送流程继续: %s", exc, exc_info=True)
        return 0


def _summarize_market_review(review_report: str, report_language: str) -> str:
    """从报告首条有效行提取一段精简的历史摘要（取前 200 字）。

    遍历报告的每一行，跳过空行、分隔线和引用块，取第一个有效文本行作为摘要。
    如果报告中没有有效内容，则返回默认提示文本。
    """
    for line in (review_report or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text and not text.startswith("---") and not text.startswith(">"):
            return text[:200]
    return "Market review report generated." if report_language == "en" else "大盘复盘报告已生成。"
