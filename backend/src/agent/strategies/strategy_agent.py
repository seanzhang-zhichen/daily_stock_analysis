# -*- coding: utf-8 -*-
"""旧版 strategy Agent 导入路径的兼容包装。

本模块仅提供从 ``src.agent.skills.skill_agent`` 的再导出，
用于保持旧版导入路径的向后兼容性。
"""

from src.agent.skills.skill_agent import SkillAgent, StrategyAgent

__all__ = ["SkillAgent", "StrategyAgent"]
