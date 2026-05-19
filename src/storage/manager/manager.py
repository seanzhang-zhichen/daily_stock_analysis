# -*- coding: utf-8 -*-
"""
``DatabaseManager`` 装配模块。

将基础设施基类与各业务 Mixin 通过多重继承组合成最终的 ``DatabaseManager``，
并暴露便捷函数 ``get_db`` 与 ``persist_llm_usage``。

业务方法按职责分散到：

- ``stock_data``  日线 / 上下文
- ``news``        新闻情报 / 基本面快照
- ``analysis_history``  分析历史
- ``conversation``      Agent 对话历史
- ``llm_usage``         LLM 调用用量
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.storage.manager._base import _DatabaseManagerBase
from src.storage.manager.analysis_history import AnalysisHistoryMixin
from src.storage.manager.conversation import ConversationMixin
from src.storage.manager.llm_usage import LLMUsageMixin
from src.storage.manager.news import NewsMixin
from src.storage.manager.stock_data import StockDataMixin


class DatabaseManager(
    StockDataMixin,
    NewsMixin,
    AnalysisHistoryMixin,
    ConversationMixin,
    LLMUsageMixin,
    _DatabaseManagerBase,
):
    """
    数据库管理器 - 单例模式
    
    职责：
    1. 管理数据库连接池
    2. 提供 Session 上下文管理
    3. 封装数据存取操作

    实际逻辑分布在 ``_DatabaseManagerBase`` 与各 Mixin 中, 此类本身仅做装配。
    """

    # 收紧类型注解到具体子类, 便于 IDE / 类型检查识别。
    _instance: Optional["DatabaseManager"] = None


# 便捷函数
def get_db() -> DatabaseManager:
    """获取数据库管理器实例的快捷方式"""
    return DatabaseManager.get_instance()


def persist_llm_usage(
    usage: Dict[str, Any],
    model: str,
    call_type: str,
    stock_code: Optional[str] = None,
) -> None:
    """Fire-and-forget: write one LLM call record to llm_usage. Never raises."""
    try:
        db = DatabaseManager.get_instance()
        db.record_llm_usage(
            call_type=call_type,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0) or 0,
            completion_tokens=usage.get("completion_tokens", 0) or 0,
            total_tokens=usage.get("total_tokens", 0) or 0,
            stock_code=stock_code,
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("[LLM usage] failed to persist usage record: %s", exc)


__all__ = ["DatabaseManager", "get_db", "persist_llm_usage"]
