# -*- coding: utf-8 -*-
"""历史分析记录查询服务层。

封装历史分析报告的查询、分页过滤、详情组装、Markdown 报告重建与新闻情报关联等逻辑，
对外供 API 层（`backend/api/v1/endpoints/history.py` 等）与前端历史记录页面调用。

主要职责：
- 历史记录分页查询与单条详情组装（含狙击点位、市场复盘原文、价格历史等）
- Markdown 报告重建：基于存储的 ``raw_result`` 还原 :class:`AnalysisResult` 并渲染成可读报告
- 新闻情报查询与按时间窗口的兜底匹配
- 触发诊断、运行时流程快照等辅助信息组装
"""
from __future__ import annotations
import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple, TYPE_CHECKING

from src.config import get_config, resolve_news_window_days
from src.report_language import (
    get_bias_status_emoji,
    get_localized_stock_name,
    get_report_labels,
    get_signal_level,
    localize_bias_status,
    localize_chip_health,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.storage import DatabaseManager
from src.utils.data_processing import normalize_model_used, parse_json_field
from src.services.run_diagnostics import build_run_diagnostic_summary

if TYPE_CHECKING:
    from src.analyzer import AnalysisResult

logger = logging.getLogger(__name__)


class MarkdownReportGenerationError(Exception):
    """当 Markdown 报告重建过程中出现内部错误时抛出。"""

    def __init__(self, message: str, record_id: str = None):
        """记录触发本次失败的 ``record_id``（可选），便于上层定位问题报告。"""
        self.message = message
        self.record_id = record_id
        super().__init__(self.message)


class HistoryService:
    """历史分析记录查询服务。

    封装针对历史分析记录的查询逻辑，统一处理 record_id 解析（兼容整型主键与 query_id 字符串）、
    详情数据组装、Markdown 报告重建与新闻情报关联等。
    """
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """初始化历史查询服务。

        Args:
            db_manager: 数据库管理器（可选，默认使用单例实例）。
        """
        self.db = db_manager or DatabaseManager.get_instance()

    def resolve_and_get_diagnostics(
        self,
        record_id: str,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """返回一份对用户友好的运行诊断摘要。

        在 ``user_id`` 下做归属校验，避免越权访问他人报告。
        """
        record = self._resolve_record(record_id, user_id=user_id)
        if not record:
            return None
        return build_run_diagnostic_summary(
            context_snapshot=parse_json_field(getattr(record, "context_snapshot", None)),
            raw_result=parse_json_field(getattr(record, "raw_result", None)),
            report_saved=True,
            query_id=getattr(record, "query_id", None),
            stock_code=getattr(record, "code", None),
        )

    def resolve_and_get_run_flow(
        self,
        record_id: str,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """返回一条历史记录对应的 A 股运行时流程快照（已脱敏）。"""
        record = self._resolve_record(record_id, user_id=user_id)
        if not record:
            return None
        from src.services.run_flow import build_history_run_flow_snapshot

        return build_history_run_flow_snapshot(record)
    
    def get_history_list(
        self,
        stock_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """获取历史分析记录列表（分页）。

        Args:
            stock_code: 按股票代码过滤。
            start_date: 起始日期，格式 ``YYYY-MM-DD``。
            end_date: 截止日期，格式 ``YYYY-MM-DD``。
            page: 页码（从 1 开始）。
            limit: 每页条数。

        Returns:
            包含 ``total`` 与 ``items`` 的字典；异常时降级返回空列表。
        """
        try:
            # Parse date parameters
            start_dt = None
            end_dt = None
            
            if start_date:
                try:
                    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
                except ValueError:
                    logger.warning(f"无效的 start_date 格式: {start_date}")
            
            if end_date:
                try:
                    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
                except ValueError:
                    logger.warning(f"无效的 end_date 格式: {end_date}")
            
            # Calculate offset
            offset = (page - 1) * limit
            
            # Use new paginated query method
            records, total = self.db.get_analysis_history_paginated(
                code=stock_code,
                start_date=start_dt,
                end_date=end_dt,
                offset=offset,
                limit=limit,
                user_id=user_id,
            )
            
            # Convert to response format
            items = []
            for record in records:
                items.append({
                    "id": record.id,
                    "query_id": record.query_id,
                    "stock_code": record.code,
                    "stock_name": record.name,
                    "report_type": record.report_type,
                    "sentiment_score": record.sentiment_score,
                    "operation_advice": record.operation_advice,
                    "created_at": record.created_at.isoformat() if record.created_at else None,
                })
            
            return {
                "total": total,
                "items": items,
            }
            
        except Exception as e:
            logger.error(f"查询历史列表失败: {e}", exc_info=True)
            return {"total": 0, "items": []}

    def _resolve_record(self, record_id: str, user_id: Optional[int] = None):
        """将 ``record_id`` 参数解析为 ``AnalysisHistory`` 对象。

        先按整型主键查询；解析失败时回退到 ``query_id`` 字符串查询。
        在 To C 模式下 ``user_id`` 会参与过滤，避免跨用户访问。

        Args:
            record_id: 整型主键（以字符串形式传入）或 ``query_id`` 字符串。

        Returns:
            ``AnalysisHistory`` 对象，未命中时返回 ``None``。
        """
        try:
            int_id = int(record_id)
            record = self.db.get_analysis_history_by_id(int_id, user_id=user_id)
            if record:
                return record
        except (ValueError, TypeError):
            pass
        # Fall back to query_id lookup; for user-scoped queries we filter via
        # ``get_analysis_history`` which honours ``user_id``.
        if user_id is not None:
            rows = self.db.get_analysis_history(query_id=record_id, limit=1, user_id=user_id)
            return rows[0] if rows else None
        return self.db.get_latest_analysis_by_query_id(record_id)

    def resolve_and_get_detail(
        self,
        record_id: str,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """解析 ``record_id``（整型主键或 ``query_id`` 字符串）并返回历史详情。

        Args:
            record_id: 整型主键（字符串形式）或 ``query_id`` 字符串。
            user_id: To C 模式下用于限定归属用户；关闭时传 ``None``。

        Returns:
            完整的分析报告字典，未命中时返回 ``None``。
        """
        try:
            record = self._resolve_record(record_id, user_id=user_id)
            if not record:
                return None
            return self._record_to_detail_dict(record)
        except Exception as e:
            logger.error(f"resolve_and_get_detail failed for {record_id}: {e}", exc_info=True)
            return None

    def resolve_and_get_news(
        self,
        record_id: str,
        limit: int = 20,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, str]]:
        """解析 ``record_id`` 并返回关联的新闻情报列表。

        Args:
            record_id: 整型主键（字符串形式）或 ``query_id`` 字符串。
            limit: 返回条数上限。
            user_id: To C 模式下用于限定归属用户。

        Returns:
            新闻情报字典列表（包含 ``title`` / ``snippet`` / ``url``）。
        """
        try:
            record = self._resolve_record(record_id, user_id=user_id)
            if not record:
                logger.warning(f"resolve_and_get_news: record not found for {record_id}")
                return []
            return self.get_news_intel(query_id=record.query_id, limit=limit)
        except Exception as e:
            logger.error(f"resolve_and_get_news failed for {record_id}: {e}", exc_info=True)
            return []

    def get_history_detail_by_id(
        self,
        record_id: int,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """通过主键 ID 获取历史报告详情。

        使用数据库主键精确查询，避免批量分析时 ``query_id`` 重复导致的错拿记录。

        Args:
            record_id: 历史记录主键 ID。
            user_id: To C 模式下用于限定归属用户。

        Returns:
            完整的分析报告字典，不存在时返回 ``None``。
        """
        try:
            record = self.db.get_analysis_history_by_id(record_id, user_id=user_id)
            if not record:
                return None
            return self._record_to_detail_dict(record)
        except Exception as e:
            logger.error(f"根据 ID 查询历史详情失败: {e}", exc_info=True)
            return None

    @staticmethod
    def _normalize_display_sniper_value(value: Any) -> Optional[str]:
        """规范化历史详情中狙击点位的展示值（剔除空值与占位符）。"""
        if value is None:
            return None
        text = str(value).strip()
        if not text or text in {"-", "—", "N/A"}:
            return None
        return text

    def _get_display_sniper_points(self, record, raw_result: Any) -> Dict[str, Optional[str]]:
        """优先使用 ``raw_result`` 中仪表盘的字符串狙击点位，回退到数值列。"""
        raw_points: Dict[str, Any] = {}
        if isinstance(raw_result, dict):
            for candidate in (raw_result.get("dashboard"), raw_result):
                if not isinstance(candidate, dict):
                    continue
                raw_points = DatabaseManager._find_sniper_in_dashboard(candidate) or raw_points
                if any(raw_points.get(k) is not None for k in ("ideal_buy", "secondary_buy", "stop_loss", "take_profit")):
                    break

        display_points: Dict[str, Optional[str]] = {}
        for field in ("ideal_buy", "secondary_buy", "stop_loss", "take_profit"):
            raw_value = self._normalize_display_sniper_value(raw_points.get(field))
            if raw_value is not None:
                display_points[field] = raw_value
                continue
            db_value = getattr(record, field, None)
            display_points[field] = str(db_value) if db_value is not None else None
        return display_points

    @staticmethod
    def _extract_market_review_content(record, raw_result: Any) -> Optional[str]:
        """从 ``raw_result`` 或 ``news_content`` 中提取大盘复盘报告的正文。"""
        if isinstance(raw_result, dict):
            for field in ("raw_response", "market_review_report"):
                content = raw_result.get(field)
                if isinstance(content, str) and content.strip():
                    return content

        news_content = getattr(record, "news_content", None)
        if isinstance(news_content, str) and news_content.strip():
            return news_content
        return None

    def _record_to_detail_dict(self, record) -> Dict[str, Any]:
        """将 ``AnalysisHistory`` ORM 记录转换为详情响应的字典。"""
        raw_result = parse_json_field(record.raw_result)

        model_used = (raw_result or {}).get("model_used") if isinstance(raw_result, dict) else None
        model_used = normalize_model_used(model_used)
        sniper_points = self._get_display_sniper_points(record, raw_result)

        context_snapshot = None
        if record.context_snapshot:
            try:
                context_snapshot = json.loads(record.context_snapshot)
            except json.JSONDecodeError:
                context_snapshot = record.context_snapshot

        market_review_content = None
        if getattr(record, "report_type", None) == "market_review":
            market_review_content = self._extract_market_review_content(record, raw_result)

        return {
            "id": record.id,
            "query_id": record.query_id,
            "stock_code": record.code,
            "stock_name": record.name,
            "report_type": record.report_type,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "model_used": model_used,
            "analysis_summary": market_review_content or record.analysis_summary,
            "operation_advice": record.operation_advice,
            "trend_prediction": record.trend_prediction,
            "sentiment_score": record.sentiment_score,
            "sentiment_label": self._get_sentiment_label(record.sentiment_score or 50),
            "ideal_buy": sniper_points.get("ideal_buy"),
            "secondary_buy": sniper_points.get("secondary_buy"),
            "stop_loss": sniper_points.get("stop_loss"),
            "take_profit": sniper_points.get("take_profit"),
            "news_content": market_review_content or record.news_content,
            "raw_result": raw_result,
            "context_snapshot": context_snapshot,
            "price_history": self._get_price_history(record.code),
        }

    def _get_price_history(self, stock_code: Optional[str], days: int = 60) -> List[Dict[str, Any]]:
        """返回近期存储的 OHLC 行，用于历史详情中的图表展示。"""
        if not stock_code or stock_code == "market_review":
            return []

        try:
            rows = list(reversed(self.db.get_latest_data(stock_code, days=days)))
        except Exception as e:
            logger.debug("get_price_history failed for %s: %s", stock_code, e, exc_info=True)
            return []

        price_history: List[Dict[str, Any]] = []
        for row in rows:
            item = row.to_dict()
            row_date = item.get("date")
            if hasattr(row_date, "isoformat"):
                item["date"] = row_date.isoformat()
            price_history.append(item)
        return price_history

    def delete_history_records(
        self,
        record_ids: List[int],
        user_id: Optional[int] = None,
    ) -> int:
        """删除指定的历史分析记录。

        Args:
            record_ids: 历史记录主键 ID 列表。
            user_id: To C 模式下用于限定归属用户，不允许跨用户删除。

        Returns:
            实际删除的记录条数。

        Raises:
            Exception: 原样抛出存储层异常，确保 API 返回正确的 500 错误而非误报成功。
        """
        return self.db.delete_analysis_history_records(record_ids, user_id=user_id)

    def get_history_trend_by_code(
        self,
        stock_code: str,
        *,
        user_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """返回某只股票按时间排列的 A 股分析结论（排除大盘复盘记录）。"""
        code = str(stock_code or "").strip()
        if not code:
            return []
        records, _ = self.db.get_analysis_history_paginated(
            code=code,
            offset=0,
            limit=max(1, min(limit, 100)),
            user_id=user_id,
        )
        return [
            {
                "id": record.id,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "sentiment_score": record.sentiment_score,
                "operation_advice": record.operation_advice,
                "trend_prediction": record.trend_prediction,
                "analysis_summary": record.analysis_summary,
                "stock_name": record.name,
            }
            for record in reversed(records)
            if record.report_type != "market_review"
        ]

    def delete_history_by_code(self, stock_code: str, *, user_id: Optional[int] = None) -> int:
        """删除某只股票下当前用户拥有的全部 A 股分析记录，返回删除条数。"""
        code = str(stock_code or "").strip()
        if not code:
            return 0
        deleted = 0
        while True:
            records, _ = self.db.get_analysis_history_paginated(
                code=code,
                offset=0,
                limit=100,
                user_id=user_id,
            )
            ids = [record.id for record in records if record.id is not None]
            if not ids:
                return deleted
            removed = self.db.delete_analysis_history_records(ids, user_id=user_id)
            if removed <= 0:
                raise RuntimeError("history deletion made no progress")
            deleted += removed

    def get_news_intel(self, query_id: str, limit: int = 20) -> List[Dict[str, str]]:
        """获取与指定 ``query_id`` 关联的新闻情报。

        Args:
            query_id: 唯一分析标识。
            limit: 返回条数上限。

        Returns:
            新闻情报列表，每条包含 ``title`` / ``snippet`` / ``url``。
        """
        try:
            records = self.db.get_news_intel_by_query_id(query_id=query_id, limit=limit)

            if not records:
                records = self._fallback_news_by_analysis_context(query_id=query_id, limit=limit)

            items: List[Dict[str, str]] = []
            for record in records:
                snippet = (record.snippet or "").strip()
                if len(snippet) > 200:
                    snippet = f"{snippet[:197]}..."
                items.append({
                    "title": record.title,
                    "snippet": snippet,
                    "url": record.url,
                })

            return items

        except Exception as e:
            logger.error(f"查询新闻情报失败: {e}", exc_info=True)
            return []

    def get_news_intel_by_record_id(self, record_id: int, limit: int = 20) -> List[Dict[str, str]]:
        """基于历史记录主键查询关联的新闻情报。

        先将 ``record_id`` 映射到 ``query_id``，再调用 :meth:`get_news_intel`。

        Args:
            record_id: 历史记录主键 ID。
            limit: 返回条数上限。

        Returns:
            新闻情报列表。
        """
        try:
            # Look up the corresponding AnalysisHistory record by record_id
            record = self.db.get_analysis_history_by_id(record_id)
            if not record:
                logger.warning(f"No analysis record found for record_id={record_id}")
                return []

            # Get query_id from record, then call original method
            return self.get_news_intel(query_id=record.query_id, limit=limit)

        except Exception as e:
            logger.error(f"根据 record_id 查询新闻情报失败: {e}", exc_info=True)
            return []

    def _fallback_news_by_analysis_context(self, query_id: str, limit: int) -> List[Any]:
        """当 ``query_id`` 直接查询未命中新闻时的兜底匹配。

        典型场景：
        - URL 级去重导致同一新闻只保留一条主记录，反复分析时需要回溯上下文。
        - 旧版本记录使用不同的 ``query_id`` 生成策略，按时间窗口与股票代码匹配更可靠。
        """
        records = self.db.get_analysis_history(query_id=query_id, limit=1)
        if not records:
            return []

        analysis = records[0]
        if not analysis.code or not analysis.created_at:
            return []

        # Narrow down to same-stock recent news, then filter by analysis time window.
        days = max(1, (datetime.now() - analysis.created_at).days + 1)
        candidates = self.db.get_recent_news(code=analysis.code, days=days, limit=max(limit * 5, 50))

        start_time = analysis.created_at - timedelta(hours=6)
        end_time = analysis.created_at + timedelta(hours=6)
        matched = [
            item for item in candidates
            if item.fetched_at and start_time <= item.fetched_at <= end_time
        ]

        # 历史兜底链路也做发布时间硬过滤，避免旧库脏数据重新冒出。
        cfg = get_config()
        window_days = resolve_news_window_days(
            news_max_age_days=getattr(cfg, "news_max_age_days", 3),
            news_strategy_profile=getattr(cfg, "news_strategy_profile", "short"),
        )
        # Anchor to analysis date instead of "today" to preserve historical context.
        anchor_date = analysis.created_at.date()
        latest_allowed = anchor_date + timedelta(days=1)
        earliest_allowed = anchor_date - timedelta(days=max(0, window_days - 1))

        filtered = []
        for item in matched:
            if not item.published_date:
                continue
            if isinstance(item.published_date, datetime):
                published = item.published_date.date()
            elif isinstance(item.published_date, date):
                published = item.published_date
            else:
                continue
            if earliest_allowed <= published <= latest_allowed:
                filtered.append(item)

        return filtered[:limit]
    
    def _get_sentiment_label(self, score: int) -> str:
        """根据情绪分数返回对应的中文情绪标签。

        Args:
            score: 情绪分数（0-100）。

        Returns:
            情绪标签字符串（极度乐观 / 乐观 / 中性 / 悲观 / 极度悲观）。
        """
        if score >= 80:
            return "极度乐观"
        elif score >= 60:
            return "乐观"
        elif score >= 40:
            return "中性"
        elif score >= 20:
            return "悲观"
        else:
            return "极度悲观"

    def get_markdown_report(
        self,
        record_id: str,
        user_id: Optional[int] = None,
    ) -> Optional[str]:
        """根据单条历史记录重建 Markdown 报告。

        从存储的 ``raw_result`` 还原 :class:`AnalysisResult`，再渲染为与推送通知一致的 Markdown 详情报告。

        Args:
            record_id: 整型主键（字符串形式）或 ``query_id`` 字符串。
            user_id: To C 模式下用于限定归属用户。

        Returns:
            渲染好的 Markdown 报告字符串，记录不存在返回 ``None``。

        Raises:
            MarkdownReportGenerationError: 重建或渲染过程中出错时抛出。
        """
        record = self._resolve_record(record_id, user_id=user_id)
        if not record:
            logger.warning(f"get_markdown_report: record not found for {record_id}")
            return None

        # Rebuild AnalysisResult from raw_result
        raw_result = parse_json_field(record.raw_result)
        if not raw_result:
            logger.error(f"get_markdown_report: raw_result is empty for {record_id}")
            raise MarkdownReportGenerationError(
                f"raw_result is empty or invalid for record {record_id}",
                record_id=record_id
            )

        if getattr(record, "report_type", None) == "market_review":
            markdown_report = self._extract_market_review_content(record, raw_result)
            if markdown_report:
                return markdown_report
            logger.error(f"get_markdown_report: market review report is empty for {record_id}")
            raise MarkdownReportGenerationError(
                f"market review report is empty for record {record_id}",
                record_id=record_id,
            )

        try:
            result = self._rebuild_analysis_result(raw_result, record)
        except Exception as e:
            logger.error(f"get_markdown_report: failed to rebuild AnalysisResult for {record_id}: {e}", exc_info=True)
            raise MarkdownReportGenerationError(
                f"Failed to rebuild AnalysisResult: {str(e)}",
                record_id=record_id
            ) from e

        if not result:
            logger.error(f"get_markdown_report: _rebuild_analysis_result returned None for {record_id}")
            raise MarkdownReportGenerationError(
                f"Failed to rebuild AnalysisResult from raw_result",
                record_id=record_id
            )

        # Generate Markdown report
        try:
            return self._generate_single_stock_markdown(result, record)
        except Exception as e:
            logger.error(f"get_markdown_report: failed to generate markdown for {record_id}: {e}", exc_info=True)
            raise MarkdownReportGenerationError(
                f"Failed to generate markdown report: {str(e)}",
                record_id=record_id
            ) from e

    def _rebuild_analysis_result(
        self,
        raw_result: Dict[str, Any],
        record
    ) -> Optional[AnalysisResult]:
        """从存储的 ``raw_result`` 字典重建 :class:`AnalysisResult` 对象。

        Args:
            raw_result: 已解析的 ``raw_result`` JSON 字典。
            record: 对应的 ``AnalysisHistory`` ORM 记录。

        Returns:
            ``AnalysisResult`` 对象或 ``None``。
        """
        try:
            from src.analyzer import AnalysisResult
            # Extract dashboard data if available
            dashboard = raw_result.get("dashboard", {})

            # Build AnalysisResult with available data
            return AnalysisResult(
                code=raw_result.get("code", record.code),
                name=raw_result.get("name", record.name),
                sentiment_score=raw_result.get("sentiment_score", record.sentiment_score or 50),
                trend_prediction=raw_result.get("trend_prediction", record.trend_prediction or ""),
                operation_advice=raw_result.get("operation_advice", record.operation_advice or ""),
                decision_type=raw_result.get("decision_type", "hold"),
                confidence_level=raw_result.get("confidence_level", "中"),
                report_language=normalize_report_language(raw_result.get("report_language")),
                dashboard=dashboard,
                trend_analysis=raw_result.get("trend_analysis", ""),
                short_term_outlook=raw_result.get("short_term_outlook", ""),
                medium_term_outlook=raw_result.get("medium_term_outlook", ""),
                technical_analysis=raw_result.get("technical_analysis", ""),
                ma_analysis=raw_result.get("ma_analysis", ""),
                volume_analysis=raw_result.get("volume_analysis", ""),
                pattern_analysis=raw_result.get("pattern_analysis", ""),
                fundamental_analysis=raw_result.get("fundamental_analysis", ""),
                sector_position=raw_result.get("sector_position", ""),
                company_highlights=raw_result.get("company_highlights", ""),
                stock_profile=raw_result.get("stock_profile") if isinstance(raw_result.get("stock_profile"), dict) else None,
                news_summary=raw_result.get("news_summary", record.news_content or ""),
                market_sentiment=raw_result.get("market_sentiment", ""),
                hot_topics=raw_result.get("hot_topics", ""),
                news_result_count=raw_result.get("news_result_count"),
                news_result_count_known=raw_result.get("news_result_count_known", "news_result_count" in raw_result),
                news_evidence_present=raw_result.get("news_evidence_present", bool(raw_result.get("news_result_count"))),
                analysis_summary=raw_result.get("analysis_summary", record.analysis_summary or ""),
                key_points=raw_result.get("key_points", ""),
                risk_warning=raw_result.get("risk_warning", ""),
                buy_reason=raw_result.get("buy_reason", ""),
                market_snapshot=raw_result.get("market_snapshot"),
                search_performed=raw_result.get("search_performed", False),
                data_sources=raw_result.get("data_sources", ""),
                success=raw_result.get("success", True),
                error_message=raw_result.get("error_message"),
                current_price=raw_result.get("current_price"),
                change_pct=raw_result.get("change_pct"),
                model_used=raw_result.get("model_used"),
            )
        except Exception as e:
            logger.error(f"Failed to rebuild AnalysisResult: {e}", exc_info=True)
            return None

    def _generate_single_stock_markdown(
        self,
        result: AnalysisResult,
        record
    ) -> str:
        """为单只股票生成 Markdown 详情报告。

        与 :meth:`NotificationService.generate_dashboard_report` 保持同一渲染模板，
        优先消费 ``dashboard`` 结构化数据。

        Args:
            result: 已重建的 :class:`AnalysisResult`。
            record: 对应的 ``AnalysisHistory`` ORM 记录。

        Returns:
            渲染好的 Markdown 报告字符串。
        """
        report_date = record.created_at.strftime("%Y-%m-%d") if record.created_at else datetime.now().strftime("%Y-%m-%d")
        report_time = record.created_at.strftime("%H:%M:%S") if record.created_at else datetime.now().strftime("%H:%M:%S")
        report_language = normalize_report_language(getattr(result, "report_language", "zh"))
        labels = get_report_labels(report_language)
        analysis_date_label = "Analysis Date" if report_language == "en" else "分析日期"
        report_time_label = "Report Time" if report_language == "en" else "报告生成时间"
        reason_label = "Rationale" if report_language == "en" else "操作理由"
        risk_warning_label = "Risk Warning" if report_language == "en" else "风险提示"
        technical_heading = "Technicals" if report_language == "en" else "技术面"
        ma_label = "Moving Averages" if report_language == "en" else "均线"
        volume_analysis_label = "Volume" if report_language == "en" else "量能"
        news_heading = "News Flow" if report_language == "en" else "消息面"

        # Escape markdown special characters in stock name
        name_escaped = self._escape_md(
            get_localized_stock_name(result.name, result.code, report_language)
        ) or result.code

        # Get signal level
        signal_text, signal_emoji, signal_tag = self._get_signal_level(result)
        dashboard = result.dashboard if hasattr(result, 'dashboard') and result.dashboard else {}

        report_lines = [
            f"# 📊 {name_escaped} ({result.code}) {labels['report_title']}",
            "",
            f"> {analysis_date_label}: **{report_date}** | {report_time_label}: {report_time}",
            "",
            "---",
            "",
        ]

        stock_profile = getattr(result, "stock_profile", None)
        if isinstance(stock_profile, dict) and stock_profile.get("research_report"):
            report_lines.extend([
                f"### 🧭 {labels['stock_profile_heading']}",
                "",
                str(stock_profile.get("research_report", "")).strip(),
                "",
            ])
            if stock_profile.get("research_method"):
                report_lines.extend([
                    f"*{labels['stock_profile_method_label']}：{stock_profile.get('research_method')}*",
                    "",
                ])

        # ========== 舆情与基本面概览（放在最前面）==========
        intel = dashboard.get('intelligence', {}) if dashboard else {}
        if intel:
            report_lines.extend([
                f"### 📰 {labels['info_heading']}",
                "",
            ])
            # 舆情情绪总结
            if intel.get('sentiment_summary'):
                report_lines.append(f"**💭 {labels['sentiment_summary_label']}**: {intel['sentiment_summary']}")
            # 业绩预期
            if intel.get('earnings_outlook'):
                report_lines.append(f"**📊 {labels['earnings_outlook_label']}**: {intel['earnings_outlook']}")
            # 风险警报（醒目显示）
            risk_alerts = intel.get('risk_alerts', [])
            if risk_alerts:
                report_lines.append("")
                report_lines.append(f"**🚨 {labels['risk_alerts_label']}**:")
                for alert in risk_alerts:
                    report_lines.append(f"- {alert}")
            # 利好催化
            catalysts = intel.get('positive_catalysts', [])
            if catalysts:
                report_lines.append("")
                report_lines.append(f"**✨ {labels['positive_catalysts_label']}**:")
                for cat in catalysts:
                    report_lines.append(f"- {cat}")
            # 最新消息
            if intel.get('latest_news'):
                report_lines.append("")
                report_lines.append(f"**📢 {labels['latest_news_label']}**: {intel['latest_news']}")
            report_lines.append("")

        # ========== 核心结论 ==========
        core = dashboard.get('core_conclusion', {}) if dashboard else {}
        one_sentence = core.get('one_sentence', result.analysis_summary)
        time_sense = core.get('time_sensitivity', labels['default_time_sensitivity'])
        pos_advice = core.get('position_advice', {})

        report_lines.extend([
            f"### 📌 {labels['core_conclusion_heading']}",
            "",
            f"**{signal_emoji} {signal_text}** | {localize_trend_prediction(result.trend_prediction, report_language)}",
            "",
            f"> **{labels['one_sentence_label']}**: {one_sentence}",
            "",
            f"⏰ **{labels['time_sensitivity_label']}**: {time_sense}",
            "",
        ])
        # 持仓分类建议
        if pos_advice:
            report_lines.extend([
                f"| {labels['position_status_label']} | {labels['action_advice_label']} |",
                "|---------|---------|",
                f"| 🆕 **{labels['no_position_label']}** | {pos_advice.get('no_position', localize_operation_advice(result.operation_advice, report_language))} |",
                f"| 💼 **{labels['has_position_label']}** | {pos_advice.get('has_position', labels['continue_holding'])} |",
                "",
            ])

        # ========== 行情快照 ==========
        self._append_market_snapshot_to_report(report_lines, result, labels)

        # ========== 数据透视 ==========
        data_persp = dashboard.get('data_perspective', {}) if dashboard else {}
        if data_persp:
            trend_data = data_persp.get('trend_status', {})
            price_data = data_persp.get('price_position', {})
            vol_data = data_persp.get('volume_analysis', {})
            chip_data = data_persp.get('chip_structure', {})

            report_lines.extend([
                f"### 📊 {labels['data_perspective_heading']}",
                "",
            ])
            # 趋势状态
            if trend_data:
                is_bullish = (
                    f"✅ {labels['yes_label']}"
                    if trend_data.get('is_bullish', False)
                    else f"❌ {labels['no_label']}"
                )
                report_lines.extend([
                    f"**{labels['ma_alignment_label']}**: {trend_data.get('ma_alignment', 'N/A')} | "
                    f"{labels['bullish_alignment_label']}: {is_bullish} | "
                    f"{labels['trend_strength_label']}: {trend_data.get('trend_score', 'N/A')}/100",
                    "",
                ])
            # 价格位置
            if price_data:
                raw_bias_status = price_data.get('bias_status', 'N/A')
                bias_status = localize_bias_status(raw_bias_status, report_language)
                bias_emoji = get_bias_status_emoji(raw_bias_status)
                report_lines.extend([
                    f"| {labels['price_metrics_label']} | {labels['current_price_label']} |",
                    "|---------|------|",
                    f"| {labels['current_price_label']} | {price_data.get('current_price', 'N/A')} |",
                    f"| {labels['ma5_label']} | {price_data.get('ma5', 'N/A')} |",
                    f"| {labels['ma10_label']} | {price_data.get('ma10', 'N/A')} |",
                    f"| {labels['ma20_label']} | {price_data.get('ma20', 'N/A')} |",
                    f"| {labels['bias_ma5_label']} | {price_data.get('bias_ma5', 'N/A')}% {bias_emoji}{bias_status} |",
                    f"| {labels['support_level_label']} | {price_data.get('support_level', 'N/A')} |",
                    f"| {labels['resistance_level_label']} | {price_data.get('resistance_level', 'N/A')} |",
                    "",
                ])
            # 量能分析
            if vol_data:
                report_lines.extend([
                    f"**{labels['volume_label']}**: {labels['volume_ratio_label']} {vol_data.get('volume_ratio', 'N/A')} "
                    f"({vol_data.get('volume_status', '')}) | {labels['turnover_rate_label']} {vol_data.get('turnover_rate', 'N/A')}%",
                    f"💡 *{vol_data.get('volume_meaning', '')}*",
                    "",
                ])
            # 筹码结构
            if chip_data:
                raw_chip_health = chip_data.get('chip_health', 'N/A')
                chip_health = localize_chip_health(raw_chip_health, report_language)
                normalized_chip_health = str(raw_chip_health or "").strip().lower()
                if normalized_chip_health in {"健康", "healthy"}:
                    chip_emoji = "✅"
                elif normalized_chip_health in {"一般", "average"}:
                    chip_emoji = "⚠️"
                else:
                    chip_emoji = "🚨"
                report_lines.extend([
                    f"**{labels['chip_label']}**: {chip_data.get('profit_ratio', 'N/A')} | {chip_data.get('avg_cost', 'N/A')} | "
                    f"{chip_data.get('concentration', 'N/A')} {chip_emoji}{chip_health}",
                    "",
                ])

        # ========== 作战计划 ==========
        battle = dashboard.get('battle_plan', {}) if dashboard else {}
        if battle:
            report_lines.extend([
                f"### 🎯 {labels['battle_plan_heading']}",
                "",
            ])
            # 狙击点位
            sniper = battle.get('sniper_points', {})
            if sniper:
                report_lines.extend([
                    f"**📍 {labels['action_points_heading']}**",
                    "",
                    f"| {labels['action_points_heading']} | {labels['current_price_label']} |",
                    "|---------|------|",
                    f"| 🎯 {labels['ideal_buy_label']} | {self._clean_sniper_value(sniper.get('ideal_buy', 'N/A'))} |",
                    f"| 🔵 {labels['secondary_buy_label']} | {self._clean_sniper_value(sniper.get('secondary_buy', 'N/A'))} |",
                    f"| 🛑 {labels['stop_loss_label']} | {self._clean_sniper_value(sniper.get('stop_loss', 'N/A'))} |",
                    f"| 🎊 {labels['take_profit_label']} | {self._clean_sniper_value(sniper.get('take_profit', 'N/A'))} |",
                    "",
                ])
            # 仓位策略
            position = battle.get('position_strategy', {})
            if position:
                report_lines.extend([
                    f"**💰 {labels['suggested_position_label']}**: {position.get('suggested_position', 'N/A')}",
                    f"- {labels['entry_plan_label']}: {position.get('entry_plan', 'N/A')}",
                    f"- {labels['risk_control_label']}: {position.get('risk_control', 'N/A')}",
                    "",
                ])
            # 检查清单
            checklist = battle.get('action_checklist', []) if battle else []
            if checklist:
                report_lines.extend([
                    f"**✅ {labels['checklist_heading']}**",
                    "",
                ])
                for item in checklist:
                    report_lines.append(f"- {item}")
                report_lines.append("")

        # ========== 如果没有 dashboard，显示传统格式 ==========
        if not dashboard:
            # 操作理由
            if result.buy_reason:
                report_lines.extend([
                    f"**💡 {reason_label}**: {result.buy_reason}",
                    "",
                ])
            # 风险提示
            if result.risk_warning:
                report_lines.extend([
                    f"**⚠️ {risk_warning_label}**: {result.risk_warning}",
                    "",
                ])
            # 技术面分析
            if result.ma_analysis or result.volume_analysis:
                report_lines.extend([
                    f"### 📊 {technical_heading}",
                    "",
                ])
                if result.ma_analysis:
                    report_lines.append(f"**{ma_label}**: {result.ma_analysis}")
                if result.volume_analysis:
                    report_lines.append(f"**{volume_analysis_label}**: {result.volume_analysis}")
                report_lines.append("")
            # 消息面
            if result.news_summary:
                report_lines.extend([
                    f"### 📰 {news_heading}",
                    f"{result.news_summary}",
                    "",
                ])

        # ========== 底部 ==========
        report_lines.extend([
            "---",
            "",
            f"*{labels['generated_at_label']}: {report_time}*",
        ])

        return "\n".join(report_lines)

    @staticmethod
    def _escape_md(text: Optional[str]) -> str:
        """转义 Markdown 特殊字符（目前处理 ``*``）。"""
        if not text:
            return ""
        return text.replace('*', r'\*')

    @staticmethod
    def _clean_sniper_value(value: Any) -> str:
        """清洗狙击点位的展示值；空值与占位符统一显示为 ``N/A``。"""
        if value is None:
            return "N/A"
        text = str(value).strip()
        if not text or text in ("-", "—", "N/A", "None"):
            return "N/A"
        return text

    def _get_signal_level(self, result: AnalysisResult) -> Tuple[str, str, str]:
        """根据情绪分数与决策类型得到对应的信号等级标签与图标。"""
        return get_signal_level(
            result.operation_advice,
            result.sentiment_score,
            getattr(result, "report_language", "zh"),
        )

    @staticmethod
    def _safe_format_number(value: Any, fmt: str = ".2f") -> str:
        """安全地格式化数值；接受可能是字符串的数字或占位符。

        Args:
            value: 待格式化值，可以是 ``int`` / ``float`` / ``str``，或 ``"N/A"`` 等。
            fmt: 数字格式串，默认 ``.2f``。

        Returns:
            格式化后的字符串；非数字值原样返回。
        """
        if value is None:
            return "N/A"
        if isinstance(value, (int, float)):
            return f"{value:{fmt}}"
        if isinstance(value, str):
            value = value.strip()
            if not value or value in ("N/A", "-", "—", "None"):
                return "N/A"
            try:
                return f"{float(value):{fmt}}"
            except (ValueError, TypeError):
                return value
        return str(value)

    @staticmethod
    def _append_market_snapshot_to_report(
        lines: List[str],
        result: AnalysisResult,
        labels: Dict[str, str],
    ) -> None:
        """向报告行缓冲中追加行情快照小节。"""
        snapshot = getattr(result, 'market_snapshot', None)
        if not snapshot:
            return

        lines.extend([
            f"### 📈 {labels['market_snapshot_heading']}",
            "",
            f"| {labels['price_metrics_label']} | {labels['current_price_label']} |",
            "|------|------|",
        ])

        # 当前价与涨跌幅：从多个可能的字段取第一个有效值
        current_price = snapshot.get('price') or snapshot.get('current_price') or result.current_price
        change_pct = snapshot.get('change_pct') or snapshot.get('pct_chg') or result.change_pct
        if current_price is not None:
            current_str = HistoryService._safe_format_number(current_price, ".2f")
            if change_pct is not None:
                if isinstance(change_pct, str) and change_pct.strip().endswith("%"):
                    # 已自带百分号，避免重复拼接
                    change_str = change_pct.strip()
                else:
                    change_str = f"{HistoryService._safe_format_number(change_pct, '+.2f')}%"
            else:
                change_str = "--"
            lines.append(f"| {labels['current_price_label']} | **{current_str}** ({change_str}) |")

        # 其他 OHLCV 指标：按统一格式补齐
        metrics = [
            (labels['open_label'], "open", ".2f"),
            (labels['high_label'], "high", ".2f"),
            (labels['low_label'], "low", ".2f"),
            (labels['volume_label'], "volume", ",.0f"),
            (labels['amount_label'], "amount", ",.0f"),
        ]
        for label, key, fmt in metrics:
            value = snapshot.get(key)
            if value is not None:
                formatted = HistoryService._safe_format_number(value, fmt)
                lines.append(f"| {label} | {formatted} |")

        lines.extend(["", "---", ""])
