# -*- coding: utf-8 -*-
"""用于构建完整配置 AgentExecutor 实例的共享工厂。

将构造逻辑集中管理，消除在 api/v1/endpoints/agent.py、bot/commands/chat.py、
bot/commands/ask.py 以及 src/core/pipeline.py 中重复的样板代码。

性能说明
--------
* ``ToolRegistry`` 只构建一次并在模块级缓存 —— 工具注册在初始化后不可变，
  因此该对象可安全地在所有请求之间共享。
* ``SkillManager`` 创建成本高（需从磁盘加载 YAML 文件）。首次使用时构建一个
  原型，之后每次请求通过廉价的 ``deepcopy`` 克隆返回，以保持线程安全
  （``activate()`` 会修改内部状态）。

用法::

    from src.agent.factory import build_agent_executor

    executor = build_agent_executor(config, skills=["bull_trend", "shrink_pullback"])
    result   = executor.chat(message="...", session_id="...")
"""

import copy
import logging
from dataclasses import dataclass
from typing import List, Optional

from src.config import AGENT_MAX_STEPS_DEFAULT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 模块级缓存
# ---------------------------------------------------------------------------
_TOOL_REGISTRY = None
_SKILL_MANAGER_PROTOTYPE = None
# 作为初始值的哨兵，让 None（即未配置自定义目录）在首次调用时被视为“已变化”，
# 从而强制构建一次，而不是被意外跳过。
_SENTINEL = object()
# 记录构建原型时使用的 custom_dir，以便在 AGENT_SKILL_DIR 运行时变化
# （例如通过配置重载）时使缓存失效。
_SKILL_MANAGER_CUSTOM_DIR: object = _SENTINEL


@dataclass
class SkillPromptState:
    """供分析入口使用的、已解析的技能激活状态与提示词片段。"""

    skill_manager: object
    skills_to_activate: List[str]
    explicit_skill_selection: bool
    use_legacy_default_prompt: bool
    skill_instructions: str
    default_skill_policy: str
    technical_skill_policy: str


def _normalize_skill_ids(
    skill_ids: Optional[List[str]],
    *,
    available_skill_ids: set[str],
) -> tuple[List[str], List[str]]:
    """返回校验通过的技能 id 以及未知 id，同时保留输入顺序。"""
    normalized: List[str] = []
    unknown: List[str] = []

    for skill_id in skill_ids or []:
        if not isinstance(skill_id, str):
            continue
        cleaned = skill_id.strip()
        if not cleaned:
            continue
        if cleaned == "all":
            if "all" not in normalized:
                normalized.append("all")
            continue
        if cleaned in available_skill_ids:
            if cleaned not in normalized:
                normalized.append(cleaned)
            continue
        if cleaned not in unknown:
            unknown.append(cleaned)

    return normalized, unknown


def normalize_requested_skill_ids(config, skill_ids: List[str]) -> List[str]:
    """校验用户提供的技能选择，但不应用默认值。"""
    skill_manager = get_skill_manager(config)
    available_skill_ids = {
        str(skill.name).strip()
        for skill in skill_manager.list_skills()
        if getattr(skill, "user_invocable", True)
    }
    normalized, unknown = _normalize_skill_ids(skill_ids, available_skill_ids=available_skill_ids)
    if unknown:
        logger.warning("[AgentFactory] Ignoring unknown requested skill ids: %s", unknown)
    return normalized


def _resolve_selected_skill_ids(
    *,
    requested_skills: Optional[List[str]],
    configured_skills: Optional[List[str]],
    default_skills: List[str],
    available_skill_ids: set[str],
) -> tuple[List[str], bool]:
    """解析生效的技能 id，以及它们是否来自有效的显式选择。"""
    selection_source = None
    raw_skill_ids = None
    if requested_skills is not None:
        selection_source = "request"
        raw_skill_ids = requested_skills
    elif configured_skills is not None:
        selection_source = "config"
        raw_skill_ids = configured_skills
    else:
        return list(default_skills), False

    selected_skill_ids, unknown_skill_ids = _normalize_skill_ids(
        raw_skill_ids,
        available_skill_ids=available_skill_ids,
    )
    if unknown_skill_ids:
        logger.warning(
            "[AgentFactory] Ignoring unknown %s skill ids: %s",
            selection_source,
            unknown_skill_ids,
        )
    if selected_skill_ids:
        return selected_skill_ids, True

    if raw_skill_ids:
        logger.warning(
            "[AgentFactory] No valid %s skills remain after validation; falling back to default skills: %s",
            selection_source,
            default_skills,
        )
    return list(default_skills), False


def _should_use_legacy_default_prompt(
    *,
    skills_to_activate: List[str],
    explicit_skill_selection: bool,
    skill_catalog: List[object],
) -> bool:
    """仅当回退到隐式内置 bull_trend 时，才沿用旧版提示词。"""
    if explicit_skill_selection or skills_to_activate != ["bull_trend"]:
        return False

    bull_trend_skill = next(
        (
            skill
            for skill in skill_catalog
            if str(getattr(skill, "name", "")).strip() == "bull_trend"
        ),
        None,
    )
    return getattr(bull_trend_skill, "source", None) == "builtin"


def get_tool_registry(config=None):
    """返回缓存的 ToolRegistry（只构建一次，跨请求共享）。"""
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY is not None:
        return _TOOL_REGISTRY

    from src.agent.tools.registry import ToolRegistry
    if config is None:
        from src.config import get_config
        config = get_config()
    from src.agent.tools.data_tools import ALL_DATA_TOOLS
    from src.agent.tools.analysis_tools import ALL_ANALYSIS_TOOLS
    from src.agent.tools.search_tools import ALL_SEARCH_TOOLS
    from src.agent.tools.market_tools import ALL_MARKET_TOOLS
    from src.agent.tools.backtest_tools import ALL_BACKTEST_TOOLS

    registry = ToolRegistry(category_timeouts={
        "data": getattr(config, "agent_data_tool_timeout_s", 0.0),
        "search": getattr(config, "agent_search_tool_timeout_s", 0.0),
        "analysis": getattr(config, "agent_analysis_tool_timeout_s", 0.0),
        "action": getattr(config, "agent_action_tool_timeout_s", 0.0),
    })
    for tool_fn in ALL_DATA_TOOLS + ALL_ANALYSIS_TOOLS + ALL_SEARCH_TOOLS + ALL_MARKET_TOOLS + ALL_BACKTEST_TOOLS:
        registry.register(tool_fn)

    _TOOL_REGISTRY = registry
    logger.info("[AgentFactory] ToolRegistry cached (%d tools)", len(registry._tools) if hasattr(registry, "_tools") else -1)
    return _TOOL_REGISTRY


def get_skill_manager(config=None):
    """返回缓存 SkillManager 原型的深拷贝克隆。

    原型在首次调用时从磁盘初始化；后续调用返回 ``copy.deepcopy(prototype)``，
    比重复读取 YAML 文件快约 10 倍。每个克隆相互独立，因此 ``.activate()``
    的调用不会在请求之间相互串扰。

    缓存失效：若 ``config.agent_skill_dir`` 在运行时变化（例如通过 Web 设置重载），
    原型会被自动重建。
    """
    global _SKILL_MANAGER_PROTOTYPE, _SKILL_MANAGER_CUSTOM_DIR

    if config is None:
        from src.config import get_config
        config = get_config()

    current_custom_dir = getattr(config, "agent_skill_dir", None)
    if _SKILL_MANAGER_PROTOTYPE is not None and current_custom_dir == _SKILL_MANAGER_CUSTOM_DIR:
        return copy.deepcopy(_SKILL_MANAGER_PROTOTYPE)

    from src.agent.skills.base import SkillManager

    if _SKILL_MANAGER_PROTOTYPE is not None:
        logger.info("[AgentFactory] SkillManager prototype invalidated (agent_skill_dir changed: %r -> %r)",
                    _SKILL_MANAGER_CUSTOM_DIR, current_custom_dir)

    skill_manager = SkillManager()
    skill_manager.load_builtin_skills()

    if current_custom_dir:
        try:
            skill_manager.load_custom_skills(current_custom_dir)
        except Exception as exc:
            logger.warning("[AgentFactory] Failed to load custom skills from %s: %s", current_custom_dir, exc)

    _SKILL_MANAGER_PROTOTYPE = skill_manager
    _SKILL_MANAGER_CUSTOM_DIR = current_custom_dir
    logger.info("[AgentFactory] SkillManager prototype cached (%d skills)", len(skill_manager._skills))
    return copy.deepcopy(_SKILL_MANAGER_PROTOTYPE)


def resolve_skill_prompt_state(config=None, skills: Optional[List[str]] = None) -> SkillPromptState:
    """为分析器/智能体入口解析生效的技能与提示词片段。"""
    if config is None:
        from src.config import get_config
        config = get_config()

    from src.agent.skills.defaults import (
        get_default_active_skill_ids,
        get_default_technical_skill_policy,
        get_default_trading_skill_policy,
    )

    skill_manager = get_skill_manager(config)
    skill_catalog = list(skill_manager.list_skills())
    available_skill_ids = {
        str(getattr(skill, "name", "")).strip()
        for skill in skill_catalog
        if str(getattr(skill, "name", "")).strip()
    }
    configured_skills = getattr(config, "agent_skills", None)
    if configured_skills == []:
        configured_skills = None
    default_skills = get_default_active_skill_ids(
        skill_catalog,
        available_skill_ids=available_skill_ids or None,
    )
    skills_to_activate, explicit_skill_selection = _resolve_selected_skill_ids(
        requested_skills=skills,
        configured_skills=configured_skills,
        default_skills=default_skills,
        available_skill_ids=available_skill_ids,
    )

    use_legacy_default_prompt = _should_use_legacy_default_prompt(
        skills_to_activate=skills_to_activate,
        explicit_skill_selection=explicit_skill_selection,
        skill_catalog=skill_catalog,
    )

    skill_manager.activate(skills_to_activate)
    logger.info("[AgentFactory] Activated skills: %s", skills_to_activate)

    return SkillPromptState(
        skill_manager=skill_manager,
        skills_to_activate=skills_to_activate,
        explicit_skill_selection=explicit_skill_selection,
        use_legacy_default_prompt=use_legacy_default_prompt,
        skill_instructions=skill_manager.get_skill_instructions(),
        default_skill_policy=get_default_trading_skill_policy(
            explicit_skill_selection=not use_legacy_default_prompt,
        ),
        technical_skill_policy=get_default_technical_skill_policy(
            explicit_skill_selection=not use_legacy_default_prompt,
        ),
    )


def build_agent_executor(config=None, skills: Optional[List[str]] = None, user_id: Optional[int] = None):
    """构建并返回一个配置完成的 AgentExecutor（或多智能体编排器）。

    当 ``AGENT_ARCH=multi`` 时，返回管理多个专用智能体的编排器；
    否则返回旧版单智能体 executor。

    Args:
        config: 应用配置对象。为 *None* 时自动调用 ``get_config()``。
        skills: 需要激活的技能 id。为 *None* 时回退到 ``config.agent_skills``；
                若该项也为空，则回退到集中的默认技能集。

    Returns:
        一个可直接调用的 :class:`src.agent.executor.AgentExecutor` 实例。
    """
    if config is None:
        from src.config import get_config
        config = get_config()

    arch = getattr(config, "agent_arch", "single")

    from src.agent.llm_adapter import LLMToolAdapter

    registry = get_tool_registry()
    prompt_state = resolve_skill_prompt_state(config, skills=skills)
    skill_manager = prompt_state.skill_manager
    logger.info(
        "[AgentFactory] Resolved skill prompt state: skills=%s (arch=%s, explicit=%s, legacy_default_prompt=%s)",
        prompt_state.skills_to_activate,
        arch,
        prompt_state.explicit_skill_selection,
        prompt_state.use_legacy_default_prompt,
    )

    llm_adapter = LLMToolAdapter(config, user_id=user_id)

    if arch == "multi":
        return _build_orchestrator(
            config,
            registry,
            llm_adapter,
            skill_manager,
            technical_skill_policy=prompt_state.technical_skill_policy,
        )

    from src.agent.executor import AgentExecutor
    return AgentExecutor(
        tool_registry=registry,
        llm_adapter=llm_adapter,
        skill_instructions=prompt_state.skill_instructions,
        default_skill_policy=prompt_state.default_skill_policy,
        use_legacy_default_prompt=prompt_state.use_legacy_default_prompt,
        max_steps=getattr(config, "agent_max_steps", AGENT_MAX_STEPS_DEFAULT),
        timeout_seconds=getattr(config, "agent_orchestrator_timeout_s", 0),
    )


def _build_orchestrator(config, registry, llm_adapter, skill_manager, *, technical_skill_policy: str = ""):
    """构建并返回 :class:`AgentOrchestrator`（多智能体模式）。

    编排器对外呈现与 :class:`AgentExecutor` 相同的 ``run()`` / ``chat()`` 接口，
    因此调用方无需任何改动。
    """
    from src.agent.orchestrator import AgentOrchestrator

    mode = getattr(config, "agent_orchestrator_mode", "standard")
    logger.info("[AgentFactory] Building AgentOrchestrator (mode=%s)", mode)

    return AgentOrchestrator(
        tool_registry=registry,
        llm_adapter=llm_adapter,
        skill_instructions=skill_manager.get_skill_instructions(),
        technical_skill_policy=technical_skill_policy,
        max_steps=getattr(config, "agent_max_steps", AGENT_MAX_STEPS_DEFAULT),
        mode=mode,
        skill_manager=skill_manager,
        config=config,
    )


# 保留旧名别名，确保使用旧名称的外部调用方仍能正常工作。
build_executor = build_agent_executor
