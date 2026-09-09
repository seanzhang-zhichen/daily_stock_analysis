# -*- coding: utf-8 -*-
"""
SkillAgent —— 针对选定技能（skill）的运行时专家适配器。

这是可选的多智能体执行层。本仓库中主要的技能抽象是
:mod:`src.agent.skills.base` 加载的指令包；该适配器仅服务于
编排器的专家（specialist）模式。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json
from src.agent.skills.defaults import build_skill_agent_name

logger = logging.getLogger(__name__)


class SkillAgent(BaseAgent):
    """对某只股票评估单个交易技能的 Agent。"""

    max_steps = 4

    def __init__(self, skill_id: Optional[str] = None, strategy_id: Optional[str] = None, **kwargs):
        """初始化一个绑定到单个技能定义的运行时 Agent。"""
        super().__init__(**kwargs)
        resolved_skill_id = skill_id or strategy_id
        if not resolved_skill_id:
            raise ValueError("skill_id is required")
        self.skill_id = resolved_skill_id
        self.agent_name = build_skill_agent_name(resolved_skill_id)
        self._skill = self._load_skill(resolved_skill_id)

        if self._skill:
            tool_names = self._skill.required_tools
            if tool_names:
                self.tool_names = list(tool_names)

    @staticmethod
    def _load_skill(skill_id: str):
        """按技能 id 加载 Skill 定义。"""
        try:
            from src.agent.factory import get_skill_manager

            sm = get_skill_manager()
            return sm.get(skill_id)
        except Exception as exc:
            logger.warning("[SkillAgent] failed to load skill '%s': %s", skill_id, exc)
        return None

    def system_prompt(self, ctx: AgentContext) -> str:
        """构造应用该特定技能指令的提示词。"""
        if self._skill:
            instructions = self._skill.instructions or self._skill.description
            display = self._skill.display_name
        else:
            instructions = f"Evaluate the '{self.skill_id}' skill."
            display = self.skill_id

        return f"""\
You are a **Skill Evaluation Agent** applying the **{display}** skill.

## Skill Instructions
{instructions}

## Task
Evaluate whether the current stock conditions satisfy this skill's entry
criteria. Use tools if needed to verify data points.

## Output Format
Return **only** a JSON object:
{{
  "skill_id": "{self.skill_id}",
  "signal": "strong_buy|buy|hold|sell|strong_sell",
  "confidence": 0.0-1.0,
  "conditions_met": ["list of satisfied conditions"],
  "conditions_missed": ["list of unsatisfied conditions"],
  "score_adjustment": -20 to +20,
  "reasoning": "2-3 sentence skill evaluation"
}}
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        """向技能评估器提供股票身份与技术上下文。"""
        parts = [
            f"Evaluate **{self.skill_id}** skill for stock "
            f"**{ctx.stock_code}** ({ctx.stock_name or 'unknown'}).",
        ]
        if ctx.opinions:
            for op in ctx.opinions:
                if op.agent_name == "technical":
                    parts.append(f"\nTechnical summary: {op.reasoning}")
                    if op.key_levels:
                        parts.append(f"Key levels: {json.dumps(op.key_levels)}")
                    if op.raw_data:
                        parts.append(
                            f"Technical data: {json.dumps(op.raw_data, ensure_ascii=False, default=str)[:2000]}"
                        )
        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        """把技能评估 JSON 解析为标准 AgentOpinion。"""
        parsed = try_parse_json(raw_text)
        if parsed is None:
            logger.warning("[SkillAgent:%s] failed to parse opinion JSON", self.skill_id)
            return None

        return AgentOpinion(
            agent_name=self.agent_name,
            signal=parsed.get("signal", "hold"),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
            raw_data=parsed,
        )


StrategyAgent = SkillAgent
