# -*- coding: utf-8 -*-
"""旧版 strategy 路由导入路径的兼容包装。

本模块仅提供从 ``src.agent.skills.router`` 的再导出，
用于保持旧版导入路径的向后兼容性。
"""

from src.agent.skills.router import SkillRouter, StrategyRouter, _DEFAULT_STRATEGIES, _DEFAULT_SKILLS

__all__ = ["SkillRouter", "StrategyRouter", "_DEFAULT_SKILLS", "_DEFAULT_STRATEGIES"]
