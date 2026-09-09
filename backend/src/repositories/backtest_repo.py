# -*- coding: utf-8 -*-
"""回测（Backtest）数据访问层。

负责回测结果（``BacktestResult``）与回测汇总（``BacktestSummary``）的查询、写入
与汇总指标计算所需的全部数据库操作，并提供候选分析记录挑选、批量保存与
分页 API 数据组装。

主要被 ``backend/src/services/backtest`` 编排的回测任务调用，并对外提供
``backend/api/v1/endpoints/backtest.py`` 所需的分页结果/汇总查询接口。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import and_, delete, desc, func, or_, select

from src.storage import BacktestResult, BacktestSummary, DatabaseManager, AnalysisHistory

logger = logging.getLogger(__name__)

# ``market_review`` 类型的分析记录不参与回测评估（用于大盘复盘类报告）。
MARKET_REVIEW_REPORT_TYPE = "market_review"


class BacktestRepository:
    """回测域的数据访问层。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """测试时注入 ``db_manager``，运行时使用进程级单例。"""
        self.db = db_manager or DatabaseManager.get_instance()

    def get_candidates(
        self,
        *,
        code: Optional[str],
        min_age_days: int,
        limit: int,
        eval_window_days: int,
        engine_version: str,
        force: bool,
        user_id: Optional[int] = None,
    ) -> List[AnalysisHistory]:
        """挑选有资格参与本次回测的 ``AnalysisHistory`` 记录。

        候选需满足：创建时间早于 ``min_age_days`` 前的截止时间、非大盘复盘类报告；
        当 ``force=False`` 时排除已存在同窗口/引擎版本回测结果的记录。
        """
        # 取「至少 N 天前」截止时间, 让结论已经经历完整评估窗口
        cutoff_dt = datetime.now() - timedelta(days=min_age_days)

        with self.db.get_session() as session:
            conditions = [AnalysisHistory.created_at <= cutoff_dt]
            if code:
                conditions.append(AnalysisHistory.code == code)
            if user_id is not None:
                conditions.append(AnalysisHistory.user_id == user_id)
            # 大盘复盘类报告不参与个股方向回测
            conditions.append(
                or_(
                    AnalysisHistory.report_type.is_(None),
                    AnalysisHistory.report_type != MARKET_REVIEW_REPORT_TYPE,
                )
            )

            query = select(AnalysisHistory).where(and_(*conditions))

            if not force:
                existing_ids = select(BacktestResult.analysis_history_id).where(
                    and_(
                        BacktestResult.eval_window_days == eval_window_days,
                        BacktestResult.engine_version == engine_version,
                    )
                )
                query = query.where(AnalysisHistory.id.not_in(existing_ids))

            query = query.order_by(desc(AnalysisHistory.created_at)).limit(limit)
            rows = session.execute(query).scalars().all()
            return list(rows)

    def save_result(self, result: BacktestResult) -> None:
        """持久化单条回测结果行。"""
        with self.db.get_session() as session:
            session.add(result)
            session.commit()

    def save_results_batch(self, results: List[BacktestResult], *, replace_existing: bool = False) -> int:
        """批量保存回测结果；``replace_existing`` 时先清空同引擎/窗口的旧记录。

        Returns:
            实际写入的记录数；批次为空时直接返回 0。
        """
        if not results:
            return 0

        with self.db.get_session() as session:
            try:
                if replace_existing:
                    analysis_ids = sorted({r.analysis_history_id for r in results if r.analysis_history_id is not None})
                    key_pairs = sorted({(r.eval_window_days, r.engine_version) for r in results})

                    if analysis_ids and key_pairs:
                        for window_days, engine_version in key_pairs:
                            session.execute(
                                delete(BacktestResult).where(
                                    and_(
                                        BacktestResult.analysis_history_id.in_(analysis_ids),
                                        BacktestResult.eval_window_days == window_days,
                                        BacktestResult.engine_version == engine_version,
                                    )
                                )
                            )

                session.add_all(results)
                session.commit()
                return len(results)
            except Exception as exc:
                session.rollback()
                logger.error(f"批量保存回测结果失败: {exc}")
                raise

    def get_results_paginated(
        self,
        *,
        code: Optional[str],
        eval_window_days: Optional[int] = None,
        engine_version: Optional[str] = None,
        analysis_date_from: Optional[date] = None,
        analysis_date_to: Optional[date] = None,
        days: Optional[int],
        offset: int,
        limit: int,
        user_id: Optional[int] = None,
    ) -> Tuple[List[Tuple[BacktestResult, Optional[str], Optional[str], Optional[datetime]]], int]:
        """分页查询回测结果，并拼接对应分析记录的展示字段。

每条返回元素为 ``(BacktestResult, name, trend_prediction, created_at)`` 四元组，
方便 API 层直接渲染列表。
        """
        with self.db.get_session() as session:
            conditions = self._build_result_conditions(
                code=code,
                eval_window_days=eval_window_days,
                engine_version=engine_version,
                analysis_date_from=analysis_date_from,
                analysis_date_to=analysis_date_to,
                days=days,
            )
            if user_id is not None:
                conditions.append(AnalysisHistory.user_id == user_id)

            where_clause = and_(*conditions) if conditions else True

            total = session.execute(
                select(func.count(BacktestResult.id))
                .select_from(BacktestResult)
                .join(AnalysisHistory, AnalysisHistory.id == BacktestResult.analysis_history_id)
                .where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(
                    BacktestResult,
                    AnalysisHistory.name,
                    AnalysisHistory.trend_prediction,
                    AnalysisHistory.created_at,
                )
                .join(AnalysisHistory, AnalysisHistory.id == BacktestResult.analysis_history_id)
                .where(where_clause)
                .order_by(desc(BacktestResult.analysis_date), desc(BacktestResult.evaluated_at))
                .offset(offset)
                .limit(limit)
            ).all()
            return list(rows), int(total)

    def count_results(
        self,
        *,
        code: Optional[str],
        eval_window_days: Optional[int] = None,
        engine_version: Optional[str] = None,
        analysis_date_from: Optional[date] = None,
        analysis_date_to: Optional[date] = None,
        days: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """统计匹配的 ``BacktestResult`` 行数，不取回任何记录。"""
        with self.db.get_session() as session:
            conditions = self._build_result_conditions(
                code=code,
                eval_window_days=eval_window_days,
                engine_version=engine_version,
                analysis_date_from=analysis_date_from,
                analysis_date_to=analysis_date_to,
                days=days,
            )
            query = select(func.count(BacktestResult.id)).select_from(BacktestResult)
            if user_id is not None:
                conditions.append(AnalysisHistory.user_id == user_id)
                query = query.join(AnalysisHistory, AnalysisHistory.id == BacktestResult.analysis_history_id)
            where_clause = and_(*conditions) if conditions else True
            count = session.execute(query.where(where_clause)).scalar() or 0
            return int(count)

    def list_results(
        self,
        *,
        code: Optional[str],
        eval_window_days: Optional[int] = None,
        engine_version: Optional[str] = None,
        analysis_date_from: Optional[date] = None,
        analysis_date_to: Optional[date] = None,
        days: Optional[int] = None,
        limit: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> List[BacktestResult]:
        """拉取匹配的回测结果行，供汇总计算或导出使用。"""
        with self.db.get_session() as session:
            conditions = self._build_result_conditions(
                code=code,
                eval_window_days=eval_window_days,
                engine_version=engine_version,
                analysis_date_from=analysis_date_from,
                analysis_date_to=analysis_date_to,
                days=days,
            )
            query = select(BacktestResult)
            if user_id is not None:
                conditions.append(AnalysisHistory.user_id == user_id)
                query = query.join(AnalysisHistory, AnalysisHistory.id == BacktestResult.analysis_history_id)
            where_clause = and_(*conditions) if conditions else True
            query = (
                query
                .where(where_clause)
                .order_by(desc(BacktestResult.analysis_date), desc(BacktestResult.evaluated_at))
            )
            if limit is not None:
                query = query.limit(limit)
            rows = session.execute(query).scalars().all()
            return list(rows)

    def upsert_summary(self, summary: BacktestSummary) -> None:
        """按唯一键 upsert 一条回测汇总行。"""
        with self.db.get_session() as session:
            existing = session.execute(
                select(BacktestSummary)
                .where(
                    and_(
                        BacktestSummary.scope == summary.scope,
                        BacktestSummary.code == summary.code,
                        BacktestSummary.eval_window_days == summary.eval_window_days,
                        BacktestSummary.engine_version == summary.engine_version,
                    )
                )
                .limit(1)
            ).scalar_one_or_none()

            if existing:
                for attr in (
                    "computed_at",
                    "total_evaluations",
                    "completed_count",
                    "insufficient_count",
                    "long_count",
                    "cash_count",
                    "win_count",
                    "loss_count",
                    "neutral_count",
                    "direction_accuracy_pct",
                    "win_rate_pct",
                    "neutral_rate_pct",
                    "avg_stock_return_pct",
                    "avg_simulated_return_pct",
                    "stop_loss_trigger_rate",
                    "take_profit_trigger_rate",
                    "ambiguous_rate",
                    "avg_days_to_first_hit",
                    "advice_breakdown_json",
                    "diagnostics_json",
                ):
                    setattr(existing, attr, getattr(summary, attr))
                session.commit()
                return

            session.add(summary)
            session.commit()

    def get_summary(
        self,
        *,
        scope: str,
        code: Optional[str],
        eval_window_days: Optional[int] = None,
        engine_version: str,
    ) -> Optional[BacktestSummary]:
        """取指定 scope/code/窗口/引擎版本下的最新汇总记录。"""
        with self.db.get_session() as session:
            conditions = [
                BacktestSummary.scope == scope,
                BacktestSummary.code == code,
                BacktestSummary.engine_version == engine_version,
            ]
            if eval_window_days is not None:
                conditions.append(BacktestSummary.eval_window_days == eval_window_days)

            row = session.execute(
                select(BacktestSummary)
                .where(and_(*conditions))
                .order_by(desc(BacktestSummary.computed_at))
                .limit(1)
            ).scalar_one_or_none()
            return row

    @staticmethod
    def parse_analysis_date_from_snapshot(context_snapshot: Optional[str]) -> Optional[date]:
        """从历史 ``context_snapshot`` JSON 中提取原始的分析日期；解析失败返回 ``None``。"""
        if not context_snapshot:
            return None

        try:
            payload = json.loads(context_snapshot)
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None

        enhanced = payload.get("enhanced_context")
        if not isinstance(enhanced, dict):
            return None

        date_str = enhanced.get("date")
        if not date_str:
            return None

        try:
            return datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    def get_distinct_eval_windows(
        self,
        *,
        code: Optional[str],
        engine_version: Optional[str] = None,
        analysis_date_from: Optional[date] = None,
        analysis_date_to: Optional[date] = None,
        user_id: Optional[int] = None,
    ) -> List[int]:
        """返回匹配条件下所有去重后的 ``eval_window_days``，按升序排列。"""
        with self.db.get_session() as session:
            conditions = self._build_result_conditions(
                code=code,
                eval_window_days=None,
                engine_version=engine_version,
                analysis_date_from=analysis_date_from,
                analysis_date_to=analysis_date_to,
                days=None,
            )
            query = select(BacktestResult.eval_window_days)
            if user_id is not None:
                conditions.append(AnalysisHistory.user_id == user_id)
                query = query.join(AnalysisHistory, AnalysisHistory.id == BacktestResult.analysis_history_id)
            where_clause = and_(*conditions) if conditions else True
            rows = session.execute(
                query
                .where(where_clause)
                .distinct()
                .order_by(BacktestResult.eval_window_days)
            ).scalars().all()
            return [int(w) for w in rows if w is not None]

    @staticmethod
    def _build_result_conditions(
        *,
        code: Optional[str],
        eval_window_days: Optional[int],
        engine_version: Optional[str],
        analysis_date_from: Optional[date],
        analysis_date_to: Optional[date],
        days: Optional[int],
    ) -> List[object]:
        """构造 list/count/窗口查询共用的 SQLAlchemy 过滤条件。"""
        conditions = []
        if code:
            conditions.append(BacktestResult.code == code)
        if eval_window_days is not None:
            conditions.append(BacktestResult.eval_window_days == eval_window_days)
        if engine_version:
            conditions.append(BacktestResult.engine_version == engine_version)
        if analysis_date_from is not None:
            conditions.append(BacktestResult.analysis_date >= analysis_date_from)
        if analysis_date_to is not None:
            conditions.append(BacktestResult.analysis_date <= analysis_date_to)
        if days:
            cutoff = datetime.now() - timedelta(days=int(days))
            conditions.append(BacktestResult.evaluated_at >= cutoff)
        return conditions
