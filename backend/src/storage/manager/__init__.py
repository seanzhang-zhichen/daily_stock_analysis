# -*- coding: utf-8 -*-
"""``DatabaseManager`` 与便捷函数的聚合入口。

本模块作为 ``src.storage.manager`` 子包的对外接口，
将 ``manager.py`` 中的核心类与函数重新导出，
使外部调用方可以通过 ``from src.storage.manager import DatabaseManager``
或 ``from src.storage import DatabaseManager`` 两种方式访问。
"""

from src.storage.manager.manager import (
    DatabaseManager,
    get_db,
    persist_llm_usage,
)

__all__ = ["DatabaseManager", "get_db", "persist_llm_usage"]
