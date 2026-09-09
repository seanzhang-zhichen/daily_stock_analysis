# -*- coding: utf-8 -*-
"""
BaseAgent —— 所有专用 Agent 的抽象基类。

多 Agent 流水线中的每个 Agent 都继承自该类并实现 :meth:`run`。
基类提供共享工具：工具子集选择、提示词组装、通过共享 runner 调用
LLM，以及结构化意见输出。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from src.agent.llm_adapter import LLMToolAdapter
from src.agent.memory import AgentMemory
from src.agent.protocols import AgentContext, AgentOpinion, StageResult, StageStatus
from src.agent.runner import RunLoopResult, run_agent_loop
from src.agent.skills.defaults import extract_skill_id
from src.agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """所有专用 Agent 的抽象基类。

    子类**必须**实现：
    - :pyattr:`agent_name` —— 唯一的 Agent 标识
    - :meth:`system_prompt` —— 返回 LLM 系统提示词
    - :meth:`build_user_message` —— 构造发给 LLM 的用户消息

    子类**可以**覆盖：
    - :pyattr:`tool_names` —— 限制 Agent 可访问的工具
    - :pyattr:`max_steps` —— 单个 Agent 的步数上限（默认 6）
    - :meth:`post_process` —— 把 LLM 原始文本转换为 :class:`AgentOpinion`
    """

    # 子类覆盖项
    agent_name: str = "base"
    tool_names: Optional[List[str]] = None  # None 表示可用全部工具
    max_steps: int = 6

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_adapter: LLMToolAdapter,
        skill_instructions: str = "",
        technical_skill_policy: str = "",
    ):
        """保存共享依赖并初始化可选的内存校准。"""
        self.tool_registry = tool_registry
        self.llm_adapter = llm_adapter
        self.skill_instructions = skill_instructions
        self.technical_skill_policy = technical_skill_policy
        self.memory = AgentMemory.from_config()

    # -----------------------------------------------------------------
    # 抽象接口
    # -----------------------------------------------------------------

    @abstractmethod
    def system_prompt(self, ctx: AgentContext) -> str:
        """构造该 Agent 的系统提示词。"""

    @abstractmethod
    def build_user_message(self, ctx: AgentContext) -> str:
        """构造发送给 LLM 的用户消息。"""

    # -----------------------------------------------------------------
    # 结构化输出的默认钩子
    # -----------------------------------------------------------------

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        """从 LLM 原始文本中提取结构化 :class:`AgentOpinion`。

        默认返回 ``None``（原始文本仍保存在 ``StageResult.meta["raw_text"]``）。
        产出分析意见的子类应覆盖此方法。
        """
        return None

    # -----------------------------------------------------------------
    # 执行
    # -----------------------------------------------------------------

    def run(
        self,
        ctx: AgentContext,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> StageResult:
        """执行该 Agent 并返回 :class:`StageResult`。

        步骤：
        1. 构造 system + user 消息。
        2. 可选地注入来自 ``ctx.data`` 的预取数据。
        3. 委托给 :func:`run_agent_loop`。
        4. 调用 :meth:`post_process` 产出意见。
        5. 把意见追加到 ``ctx.opinions``。
        """
        t0 = time.time()
        result = StageResult(stage_name=self.agent_name, status=StageStatus.RUNNING)

        try:
            messages = self._build_messages(ctx)

            # 若 Agent 声明了工具子集，则限制可用工具
            registry = self._filtered_registry()

            loop_result: RunLoopResult = run_agent_loop(
                messages=messages,
                tool_registry=registry,
                llm_adapter=self.llm_adapter,
                max_steps=self.max_steps,
                progress_callback=progress_callback,
                max_wall_clock_seconds=timeout_seconds,
                emit_stage_events=False,
            )

            result.tokens_used = loop_result.total_tokens
            result.tool_calls_count = len(loop_result.tool_calls_log)
            result.meta["raw_text"] = loop_result.content
            result.meta["models_used"] = loop_result.models_used
            result.meta["tool_calls_log"] = loop_result.tool_calls_log

            if not loop_result.success:
                result.status = StageStatus.FAILED
                result.error = loop_result.error or "Agent loop did not produce a final answer"
                return result

            # 后处理为结构化意见
            opinion = self.post_process(ctx, loop_result.content)
            if opinion is not None:
                opinion.agent_name = self.agent_name
                self._apply_memory_calibration(ctx, opinion, result)
                ctx.add_opinion(opinion)
                result.opinion = opinion

            result.status = StageStatus.COMPLETED

        except Exception as exc:
            logger.error("[%s] execution failed: %s", self.agent_name, exc, exc_info=True)
            result.status = StageStatus.FAILED
            result.error = str(exc)
        finally:
            result.duration_s = round(time.time() - t0, 2)

        return result

    # -----------------------------------------------------------------
    # 内部辅助方法
    # -----------------------------------------------------------------

    def _build_messages(self, ctx: AgentContext) -> List[Dict[str, Any]]:
        """组装发送给 LLM 的初始消息列表。"""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt(ctx)},
        ]

        history = ctx.meta.get("conversation_history")
        if isinstance(history, list):
            for message in history:
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                content = message.get("content")
                if role in {"user", "assistant", "system"} and isinstance(content, str) and content:
                    messages.append({"role": role, "content": content})

        # 把预取数据注入为一条合成的 assistant 上下文消息
        cached_data = self._inject_cached_data(ctx)
        if cached_data:
            messages.append({"role": "user", "content": cached_data})
            messages.append({"role": "assistant", "content": "Understood, I have the pre-fetched data. Proceeding with analysis."})

        messages.append({"role": "user", "content": self.build_user_message(ctx)})
        return messages

    def _inject_cached_data(self, ctx: AgentContext) -> str:
        """由 ``ctx.data`` 中已取到的数据构造上下文字符串。

        当前置阶段已取好该 Agent 所需数据时，可避免重复工具调用。
        """
        import json
        parts: List[str] = []
        for key, value in ctx.data.items():
            if value is not None:
                try:
                    serialised = json.dumps(value, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    serialised = str(value)
                # 限制单字段大小，避免撑爆上下文窗口
                max_chars = 30000 if key == "stock_profile" else 8000
                if len(serialised) > max_chars:
                    serialised = serialised[:max_chars] + "...(truncated)"
                parts.append(f"[Pre-fetched: {key}]\n{serialised}")
        memory_context = self._build_memory_context(ctx)
        if memory_context:
            parts.append(memory_context)
        return "\n\n".join(parts) if parts else ""

    def _filtered_registry(self) -> ToolRegistry:
        """返回限定为 ``self.tool_names`` 的工具注册表。

        若 ``tool_names`` 为 None（默认值），则返回完整注册表。
        """
        if self.tool_names is None:
            return self.tool_registry

        from src.agent.tools.registry import ToolRegistry as TR
        filtered = TR(category_timeouts=self.tool_registry.category_timeouts)
        for name in self.tool_names:
            tool_def = self.tool_registry.get(name)
            if tool_def:
                filtered.register(tool_def)
            else:
                logger.warning("[%s] requested tool '%s' not found in registry", self.agent_name, name)
        return filtered

    def _build_memory_context(self, ctx: AgentContext) -> str:
        """汇总近期分析历史，用于提示词注入。"""
        if not self.memory.enabled or not ctx.stock_code:
            return ""

        entries = self.memory.get_stock_history(ctx.stock_code, limit=3)
        if not entries:
            return ""

        lines = ["[Memory: recent analysis history]"]
        for entry in entries:
            parts = [
                entry.date or "unknown_date",
                f"signal={entry.signal or 'unknown'}",
                f"sentiment={entry.sentiment_score}",
            ]
            if entry.price_at_analysis:
                parts.append(f"price={entry.price_at_analysis}")
            if entry.outcome_5d is not None:
                parts.append(f"outcome_5d={entry.outcome_5d}")
            if entry.outcome_20d is not None:
                parts.append(f"outcome_20d={entry.outcome_20d}")
            if entry.was_correct is not None:
                parts.append(f"was_correct={entry.was_correct}")
            lines.append("- " + ", ".join(parts))
        lines.append("Use this memory as context only; do not copy it verbatim into the final answer.")
        return "\n".join(lines)

    def _apply_memory_calibration(self, ctx: AgentContext, opinion: AgentOpinion, result: StageResult) -> None:
        """启用时依据历史校准调整置信度。"""
        if not self.memory.enabled:
            return

        skill_id = extract_skill_id(self.agent_name)
        calibration = self.memory.get_calibration(
            agent_name=self.agent_name,
            stock_code=ctx.stock_code or None,
            skill_id=skill_id,
        )
        if not calibration.calibrated:
            return

        raw_confidence = opinion.confidence
        opinion.confidence = max(0.0, min(1.0, raw_confidence * calibration.calibration_factor))
        result.meta["memory_calibration"] = {
            "raw_confidence": raw_confidence,
            "calibrated_confidence": opinion.confidence,
            "factor": calibration.calibration_factor,
            "samples": calibration.total_samples,
        }
