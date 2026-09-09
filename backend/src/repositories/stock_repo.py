# -*- coding: utf-8 -*-
"""
===================================
股票数据访问层
===================================

负责 ``StockDaily`` 表（股票日线数据）的 CRUD 与常用查询，并提供回测所需的
起点日线和前向日线数据。
"""

import logging
from datetime import date
from typing import Optional, List, Dict, Any

import pandas as pd
from sqlalchemy import and_, desc, select

from src.storage import DatabaseManager, StockDaily

logger = logging.getLogger(__name__)


class StockRepository:
    """
    股票数据访问层。

    封装 ``StockDaily`` 表的数据库操作，提供 ``get_latest`` / ``get_range`` 等
    便捷查询，并把 DataFrame 写入、上下文组装、前向日线获取等回测与分析能力
    委托给 ``DatabaseManager``。
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """
        初始化数据访问层。

        Args:
            db_manager: 数据库管理器（可选，默认使用单例）。
        """
        self.db = db_manager or DatabaseManager.get_instance()

    def get_latest(self, code: str, days: int = 2) -> List[StockDaily]:
        """
        获取最近 N 天的数据（按日期降序）。

        Args:
            code: 股票代码。
            days: 获取天数。

        Returns:
            StockDaily 对象列表。
        """
        try:
            return self.db.get_latest_data(code, days)
        except Exception as e:
            logger.error(f"获取最新数据失败: {e}")
            return []

    def get_range(
        self,
        code: str,
        start_date: date,
        end_date: date
    ) -> List[StockDaily]:
        """
        获取指定日期范围的数据（按日期升序）。

        Args:
            code: 股票代码。
            start_date: 开始日期。
            end_date: 结束日期。

        Returns:
            StockDaily 对象列表。
        """
        try:
            return self.db.get_data_range(code, start_date, end_date)
        except Exception as e:
            logger.error(f"获取日期范围数据失败: {e}")
            return []

    def save_dataframe(
        self,
        df: pd.DataFrame,
        code: str,
        data_source: str = "Unknown",
        canonical_id: Optional[str] = None,
    ) -> int:
        """
        保存 DataFrame 到数据库。

        Args:
            df: 包含日线数据的 DataFrame。
            code: 股票代码。
            data_source: 数据来源。
            canonical_id: 规范化的稳定分析 ID（可选）。

        Returns:
            实际新增的记录数（不含更新覆盖部分）。
        """
        try:
            return self.db.save_daily_data(df, code, data_source, canonical_id=canonical_id)
        except Exception as e:
            logger.error(f"保存日线数据失败: {e}")
            return 0

    def has_today_data(self, code: str, target_date: Optional[date] = None) -> bool:
        """
        检查指定日期是否已有日线数据。

        Args:
            code: 股票代码。
            target_date: 目标日期（默认今天）。

        Returns:
            是否存在数据；查询失败返回 False。
        """
        try:
            return self.db.has_today_data(code, target_date)
        except Exception as e:
            logger.error(f"检查数据存在失败: {e}")
            return False

    def get_analysis_context(
        self,
        code: str,
        target_date: Optional[date] = None
    ) -> Optional[Dict[str, Any]]:
        """
        获取分析所需的上下文数据（今日数据 + 昨日对比）。

        Args:
            code: 股票代码。
            target_date: 目标日期。

        Returns:
            包含今日/昨日/变化率等信息的字典；不存在则返回 ``None``。
        """
        try:
            return self.db.get_analysis_context(code, target_date)
        except Exception as e:
            logger.error(f"获取分析上下文失败: {e}")
            return None

    def get_start_daily(self, *, code: str, analysis_date: date) -> Optional[StockDaily]:
        """返回 ``analysis_date`` 当日的 ``StockDaily``（缺则回退到最近一个更早交易日）。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date <= analysis_date))
                .order_by(desc(StockDaily.date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def get_forward_bars(self, *, code: str, analysis_date: date, eval_window_days: int) -> List[StockDaily]:
        """返回 ``analysis_date`` 之后最多 ``eval_window_days`` 个交易日的日线（按日期升序）。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date > analysis_date))
                .order_by(StockDaily.date)
                .limit(eval_window_days)
            ).scalars().all()
            return list(rows)
