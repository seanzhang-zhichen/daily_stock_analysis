# -*- coding: utf-8 -*-
"""旧版 strategy 聚合器导入路径的兼容包装。

本模块仅提供从 ``src.agent.skills.aggregator`` 的再导出，
用于保持旧版导入路径的向后兼容性。
"""

from src.agent.skills.aggregator import SkillAggregator, StrategyAggregator

__all__ = ["SkillAggregator", "StrategyAggregator"]
