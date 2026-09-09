# -*- coding: utf-8 -*-
"""
===================================
报告引擎 - 历史对比服务
===================================

为报告渲染拉取每只股票近期的分析信号变化，并通过 exclude_query_id
排除当前这条记录本身。
"""

import logging
from typing import Any, Dict, List, Optional

from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


def _record_to_signal(record: Any) -> Optional[Dict[str, Any]]:
    """把 AnalysisHistory 记录转换为信号字典；解析出错时跳过。"""
    try:
        return {
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "query_id": record.query_id,
            "sentiment_score": record.sentiment_score,
            "operation_advice": record.operation_advice,
            "trend_prediction": record.trend_prediction,
        }
    except Exception as e:
        logger.debug("Skip record for history comparison: %s", e)
        return None


def get_signal_changes(
    code: str,
    limit: int = 5,
    exclude_query_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    获取单只股票近期的信号变化。

    Args:
        code: 股票代码
        limit: 最多返回的记录数
        exclude_query_id: 排除该 query_id 对应的记录（例如当前这次运行）

    Returns:
        信号字典列表（created_at、sentiment_score、operation_advice、trend_prediction）
    """
    db = DatabaseManager.get_instance()
    records = db.get_analysis_history(
        code=code,
        days=90,
        limit=limit,
        exclude_query_id=exclude_query_id,
    )
    out = []
    for r in records:
        sig = _record_to_signal(r)
        if sig:
            out.append(sig)
    return out


def get_signal_changes_batch(
    codes: List[str],
    limit: int = 5,
    exclude_query_ids: Optional[Dict[str, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    获取多只股票近期的信号变化。

    Args:
        codes: 股票代码列表
        limit: 每只股票最多返回的记录数
        exclude_query_ids: 代码 -> query_id 的映射，用于逐只股票排除对应记录

    Returns:
        代码 -> 信号字典列表 的映射
    """
    exclude_query_ids = exclude_query_ids or {}
    db = DatabaseManager.get_instance()
    result: Dict[str, List[Dict[str, Any]]] = {c: [] for c in codes}
    for code in codes:
        exclude = exclude_query_ids.get(code)
        records = db.get_analysis_history(
            code=code,
            days=90,
            limit=limit,
            exclude_query_id=exclude,
        )
        for r in records:
            sig = _record_to_signal(r)
            if sig:
                result[code].append(sig)
    return result
