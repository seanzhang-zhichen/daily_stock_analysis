# -*- coding: utf-8 -*-
"""
===================================
分析历史数据访问层
===================================

封装 ``AnalysisHistory`` 表的查询与持久化操作，供 API 与 Agent 等上层调用。

``user_id`` 用于 To C 多用户隔离，调用方按当前 ``AppUser`` 注入；单租户模式
下传 ``None`` 即不过滤。

主要功能：
- 根据 query_id 获取单条分析记录（get_by_query_id）
- 获取分析记录列表（get_list）
- 保存分析结果历史（save）
- 统计指定股票在时间窗内的分析记录数（count_by_code）
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from src.storage import DatabaseManager, AnalysisHistory

# 配置日志记录器：用于输出模块级别的调试、信息、警告和错误日志
logger = logging.getLogger(__name__)


class AnalysisRepository:
    """
    分析历史数据访问层。

    封装 ``AnalysisHistory`` 表的数据库操作，主要提供按 ``query_id`` / 时间窗
    / 用户维度的查询，以及分析历史条目的保存与计数能力。

    使用方式：
        repo = AnalysisRepository()  # 使用默认数据库管理器单例
        record = repo.get_by_query_id("query_123", user_id=1)
        repo.save(result, "query_123", "daily_report")
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """
        初始化分析历史数据访问层。

        Args:
            db_manager: 数据库管理器（可选，默认使用单例）。
                        如果未提供，则通过 DatabaseManager.get_instance() 获取默认实例。
        """
        self.db = db_manager or DatabaseManager.get_instance()

    def get_by_query_id(
        self,
        query_id: str,
        user_id: Optional[int] = None,
    ) -> Optional[AnalysisHistory]:
        """
        根据 ``query_id`` 获取单条分析记录（取最新一条）。

        该方法用于根据查询ID获取对应的分析历史记录。由于同一个 query_id
        可能对应多条记录（如重复分析），该方法只返回最新的一条。

        Args:
            query_id: 查询 ID，由调用方生成的唯一标识。
            user_id: To C 模式下传入限定归属用户；关闭时传 ``None``。
                     单租户模式下传 ``None`` 即不过滤用户。

        Returns:
            ``AnalysisHistory`` 对象，不存在返回 ``None``。
        """
        try:
            records = self.db.get_analysis_history(
                query_id=query_id,
                limit=1,
                user_id=user_id,
            )
            return records[0] if records else None
        except Exception as e:
            logger.error(f"查询分析记录失败: {e}")
            return None

    def get_list(
        self,
        code: Optional[str] = None,
        days: int = 30,
        limit: int = 50,
        user_id: Optional[int] = None,
    ) -> List[AnalysisHistory]:
        """
        获取分析记录列表。

        根据股票代码、时间范围和用户ID获取分析历史记录列表。
        返回结果按时间倒序排列，最多返回 limit 条记录。

        Args:
            code: 股票代码筛选，如 "600519"。传 ``None`` 表示不筛选股票。
            days: 时间范围（天），默认30天。只返回最近 days 天内的记录。
            limit: 返回数量限制，默认50条。
            user_id: To C 模式下传入限定归属用户；关闭时传 ``None``。
                     单租户模式下传 ``None`` 即不过滤用户。

        Returns:
            ``AnalysisHistory`` 对象列表。查询失败返回空列表。
        """
        try:
            return self.db.get_analysis_history(
                code=code,
                days=days,
                limit=limit,
                user_id=user_id,
            )
        except Exception as e:
            logger.error(f"获取分析列表失败: {e}")
            return []

    def save(
        self,
        result: Any,
        query_id: str,
        report_type: str,
        news_content: Optional[str] = None,
        context_snapshot: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """
        保存一条分析结果历史。

        将分析结果持久化到数据库中，支持保存分析结果、查询ID、报告类型、
        新闻内容和上下文快照等信息。常用于分析完成后保存结果供后续查询。

        Args:
            result: 分析结果对象，可以是任意可序列化的数据结构。
            query_id: 查询 ID，由调用方生成的唯一标识。
            report_type: 报告类型，如 "daily_report", "weekly_report", "realtime" 等。
            news_content: 新闻内容（可选），用于关联相关新闻。
            context_snapshot: 上下文快照（可选），保存分析时的上下文信息，
                              如市场数据、配置参数等。
            user_id: To C 模式下绑定归属用户；关闭时传 ``None`` 保持单租户行为。

        Returns:
            实际写入的记录数；失败时返回 0。
        """
        try:
            return self.db.save_analysis_history(
                result=result,
                query_id=query_id,
                report_type=report_type,
                news_content=news_content,
                context_snapshot=context_snapshot,
                user_id=user_id,
            )
        except Exception as e:
            logger.error(f"保存分析结果失败: {e}")
            return 0

    def count_by_code(
        self,
        code: str,
        days: int = 30,
        user_id: Optional[int] = None,
    ) -> int:
        """
        统计指定股票在时间窗内的分析记录数。

        用于统计某只股票在指定时间范围内的分析记录数量，
        常用于判断是否需要重新分析或分析频率统计。

        Args:
            code: 股票代码，如 "600519"。
            days: 时间范围（天），默认30天。只统计最近 days 天内的记录。
            user_id: To C 模式下限定归属用户；关闭时传 ``None``。
                     单租户模式下传 ``None`` 即不过滤用户。

        Returns:
            记录数量；查询失败返回 0。
        """
        try:
            records = self.db.get_analysis_history(
                code=code,
                days=days,
                limit=1000,
                user_id=user_id,
            )
            return len(records)
        except Exception as e:
            logger.error(f"统计分析记录失败: {e}")
            return 0
