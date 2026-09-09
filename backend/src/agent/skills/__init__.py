# -*- coding: utf-8 -*-
"""Agent 技能（skills）子系统包入口。

提供"可插拔的交易策略技能"：

- 技能本身以自然语言（YAML / Markdown）描述，无需写 Python 代码；
- 通过 :class:`SkillManager` 统一管理生命周期；
- 通过 :class:`SkillAgent` / :class:`SkillRouter` / :class:`SkillAggregator`
  把技能挂入 Agent 流水线。

注意：本包对外暴露的常量与函数大多**惰性导入**，避免 ``agent`` 与 ``skills``
子包互相引用导致循环依赖。
"""

from src.agent.skills.base import (
    Skill,
    SkillManager,
    load_skill_from_markdown,
    load_skill_from_yaml,
    load_skills_from_directory,
)
from src.agent.skills.defaults import (
    DEFAULT_ACTIVE_SKILL_IDS,
    DEFAULT_ROUTER_SKILL_IDS,
    PRIMARY_DEFAULT_SKILL_ID,
    CORE_TRADING_SKILL_POLICY_ZH,
    TECHNICAL_SKILL_RULES_EN,
    get_default_active_skill_ids,
    get_default_router_skill_ids,
    get_primary_default_skill_id,
    get_regime_skill_ids,
)

__all__ = [
    "Skill",
    "SkillManager",
    "SkillAgent",
    "SkillRouter",
    "SkillAggregator",
    "DEFAULT_ACTIVE_SKILL_IDS",
    "DEFAULT_ROUTER_SKILL_IDS",
    "PRIMARY_DEFAULT_SKILL_ID",
    "CORE_TRADING_SKILL_POLICY_ZH",
    "TECHNICAL_SKILL_RULES_EN",
    "get_default_active_skill_ids",
    "get_default_router_skill_ids",
    "get_primary_default_skill_id",
    "get_regime_skill_ids",
    "load_skill_from_markdown",
    "load_skill_from_yaml",
    "load_skills_from_directory",
]


def __getattr__(name):
    """PEP 562 风格的惰性属性加载，避免包导入时的循环依赖。

    重型组件（``SkillAgent`` / ``SkillRouter`` / ``SkillAggregator``）只在
    第一次被访问时才真正导入，从而让 ``from src.agent.skills import Skill``
    这种轻量用法不会触发整个子包初始化。

    Args:
        name: 调用方访问的属性名。

    Returns:
        对应的类对象。

    Raises:
        AttributeError: 属性名不在惰性白名单中时抛出。
    """
    if name == "SkillAgent":
        from src.agent.skills.skill_agent import SkillAgent

        return SkillAgent
    if name == "SkillRouter":
        from src.agent.skills.router import SkillRouter

        return SkillRouter
    if name == "SkillAggregator":
        from src.agent.skills.aggregator import SkillAggregator

        return SkillAggregator
    # 未识别属性走标准 AttributeError，让 IDE / 调试器获得一致行为
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
