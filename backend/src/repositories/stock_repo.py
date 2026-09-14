# -*- coding: utf-8 -*-
"""
===================================
股票数据访问层
===================================

负责 ``StockDaily`` 表（股票日线数据）的 CRUD 与常用查询，并提供回测所需的
起点日线和前向日线数据。

主要功能：
- 获取最近N天数据（get_latest）
- 获取指定日期范围数据（get_range）
- 保存DataFrame到数据库（save_dataframe）
- 检查指定日期数据是否存在（has_today_data）
- 获取分析上下文（get_analysis_context）
- 获取回测起点日线（get_start_daily）
- 获取前向日线（get_forward_bars）
"""

import logging
from datetime import date
from typing import Optional, List, Dict, Any

import pandas as pd
from sqlalchemy import and_, desc, select

from src.storage import DatabaseManager, StockDaily

# 配置日志记录器：用于输出模块级别的调试、信息、警告和错误日志
logger = logging.getLogger(__name__)


class StockRepository:
    """
    股票数据访问层。

    封装 ``StockDaily`` 表的数据库操作，提供 ``get_latest`` / ``get_range`` 等
    便捷查询，并把 DataFrame 写入、上下文组装、前向日线获取等回测与分析能力
    委托给 ``DatabaseManager``。

    使用方式：
        repo = StockRepository()  # 使用默认数据库管理器单例
        latest_data = repo.get_latest("600519", days=5)
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """
        初始化数据访问层。

        Args:
            db_manager: 数据库管理器（可选，默认使用单例）。
                        如果未提供，则通过 DatabaseManager.get_instance() 获取默认实例。
        """
        self.db = db_manager or DatabaseManager.get_instance()

    def get_latest(self, code: str, days: int = 2) -> List[StockDaily]:
        """
        获取最近 N 天的数据（按日期降序）。

        该方法用于获取指定股票最近N个交易日的日线数据，返回结果按日期降序排列。
        常用于获取最新行情或近期走势分析。

        Args:
            code: 股票代码，如 "600519"（贵州茅台）。
            days: 获取天数，默认为2天（今天和昨天）。

        Returns:
            StockDaily 对象列表，按日期降序排列。查询失败返回空列表。
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

        该方法用于获取指定股票在[start_date, end_date]区间内的日线数据，
        返回结果按日期升序排列。常用于历史数据分析和回测。

        Args:
            code: 股票代码，如 "600519"。
            start_date: 开始日期（包含）。
            end_date: 结束日期（包含）。

        Returns:
            StockDaily 对象列表，按日期升序排列。查询失败返回空列表。
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

        将包含日线数据的 DataFrame 持久化到数据库中，支持指定数据来源和规范化ID。
        该方法会自动处理重复数据（根据日期和代码去重），返回实际新增的记录数。

        Args:
            df: 包含日线数据的 DataFrame，必须包含 date, open, high, low, close, volume 等列。
            code: 股票代码。
            data_source: 数据来源标识，如 "Tushare", "Akshare" 等，用于追踪数据出处。
            canonical_id: 规范化的稳定分析 ID（可选），用于关联不同来源的同一股票。

        Returns:
            实际新增的记录数（不含更新覆盖部分）。保存失败返回0。
        """
        try:
            return self.db.save_daily_data(df, code, data_source, canonical_id=canonical_id)
        except Exception as e:
            logger.error(f"保存日线数据失败: {e}")
            return 0

    def has_today_data(self, code: str, target_date: Optional[date] = None) -> bool:
        """
        检查指定日期是否已有日线数据。

        用于判断某只股票在指定日期是否已经有数据，避免重复拉取。
        常用于数据同步前的存在性检查。

        Args:
            code: 股票代码，如 "600519"。
            target_date: 目标日期（默认今天），格式为 datetime.date。

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

        该方法组装分析所需的上下文信息，包括今日数据、昨日数据、涨跌幅、
        成交量变化等关键指标。常用于生成分析报告前的数据准备。

        Args:
            code: 股票代码，如 "600519"。
            target_date: 目标日期，默认为今天。

        Returns:
            包含今日/昨日/变化率等信息的字典；不存在则返回 ``None``。
            典型返回结构：
            {
                "today": StockDaily,
                "yesterday": StockDaily,
                "change_pct": float,  # 涨跌幅(%)
                "volume_change_pct": float  # 成交量变化(%)
            }
        """
        try:
            return self.db.get_analysis_context(code, target_date)
        except Exception as e:
            logger.error(f"获取分析上下文失败: {e}")
            return None

    def get_start_daily(self, *, code: str, analysis_date: date) -> Optional[StockDaily]:
        """
        返回 ``analysis_date`` 当日的 ``StockDaily``（缺则回退到最近一个更早交易日）。

        该方法用于回测时确定分析起点：如果 analysis_date 当天有数据，则返回当天数据；
        如果没有，则回退到最近的一个有数据的交易日。这是回测引擎的关键入口。

        Args:
            code: 股票代码，如 "600519"。
            analysis_date: 分析目标日期。

        Returns:
            analysis_date 当日或最近更早交易日的 StockDaily 记录；
            如果该股票没有任何历史数据则返回 None。
        """
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date <= analysis_date))
                .order_by(desc(StockDaily.date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def get_forward_bars(self, *, code: str, analysis_date: date, eval_window_days: int) -> List[StockDaily]:
        """
        返回 ``analysis_date`` 之后最多 ``eval_window_days`` 个交易日的日线（按日期升序）。

        该方法用于回测时获取分析日期之后的未来数据，用于评估策略表现。
        返回的数据按日期升序排列，最多返回 eval_window_days 条记录。

        Args:
            code: 股票代码，如 "600519"。
            analysis_date: 分析基准日期，只返回该日期之后的记录。
            eval_window_days: 评估窗口天数，即最多返回多少天的数据。

        Returns:
            StockDaily 对象列表，按日期升序排列。如果 analysis_date 之后没有数据则返回空列表。
        """
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date > analysis_date))
                .order_by(StockDaily.date)
                .limit(eval_window_days)
            ).scalars().all()
            return list(rows)
