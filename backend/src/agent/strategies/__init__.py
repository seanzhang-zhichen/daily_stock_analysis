# -*- coding: utf-8 -*-
"""
旧版 strategy 命名空间的兼容性再导出。

提供：
- :class:`StrategyAgent` —— :class:`SkillAgent` 的旧版别名
- :class:`StrategyRouter` —— :class:`SkillRouter` 的旧版别名
- :class:`StrategyAggregator` —— :class:`SkillAggregator` 的旧版别名

.. note::
    本模块仅用于向后兼容，新代码应直接使用 ``src.agent.skills`` 下的类。
"""

from src.agent.strategies.strategy_agent import StrategyAgent
from src.agent.strategies.router import StrategyRouter
from src.agent.strategies.aggregator import StrategyAggregator

__all__ = [
    "StrategyAgent",
    "StrategyRouter",
    "StrategyAggregator",
]
