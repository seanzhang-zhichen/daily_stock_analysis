# -*- coding: utf-8 -*-
"""
AgentOrchestrator —— 多 Agent 流水线协调器。

负责管理单只股票分析运行中各个专职 Agent（Technical → Intel → Risk →
Specialist → Decision）的生命周期。

运行模式：
- ``quick``   : 仅 Technical → Decision（最快，约 2 次 LLM 调用）
- ``standard``: Technical → Intel → Decision（默认）
- ``full``    : Technical → Intel → Risk → Decision
- ``specialist``: Technical → Intel → Risk → 专家评估 → Decision

协调器的职责：
1. 用用户查询与股票代码初始化 :class:`AgentContext`
2. 依次运行各 Agent，传递共享上下文
3. 收集每个 Agent 的 :class:`StageResult`
4. 汇总为统一的 :class:`OrchestratorResult` 并生成最终仪表盘

关键点：本类对外暴露与 ``AgentExecutor`` 相同的 ``run(task, context)`` 和
``chat(message, session_id, ...)`` 接口，因此可以通过工厂直接替换使用。
"""

from __future__ import annotations

import json
import inspect
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from src.agent.llm_adapter import LLMToolAdapter
from src.agent.protocols import (
    AgentContext,
    AgentRunStats,
    StageResult,
    StageStatus,
    normalize_decision_signal,
)
from src.agent.runner import parse_dashboard_json
from src.agent.skills.defaults import extract_skill_id, is_skill_agent_name
from src.agent.tools.registry import ToolRegistry
from src.config import AGENT_MAX_STEPS_DEFAULT
from src.report_language import normalize_report_language

if TYPE_CHECKING:
    from src.agent.executor import AgentResult

logger = logging.getLogger(__name__)

# 合法的协调器运行模式（按成本/深度排序）
VALID_MODES = ("quick", "standard", "full", "specialist")


@dataclass
class OrchestratorResult:
    """多 Agent 流水线一次运行的统一结果。"""

    success: bool = False
    content: str = ""
    dashboard: Optional[Dict[str, Any]] = None
    tool_calls_log: List[Dict[str, Any]] = field(default_factory=list)
    total_steps: int = 0
    total_tokens: int = 0
    provider: str = ""
    model: str = ""
    error: Optional[str] = None
    stats: Optional[AgentRunStats] = None
    skill_opinions: List[Dict[str, Any]] = field(default_factory=list)


class AgentOrchestrator:
    """多 Agent 流水线协调器。

    是 ``AgentExecutor`` 的直接替代实现——暴露相同的 ``run()`` 与 ``chat()``
    接口，工厂通过 ``AGENT_ARCH`` 在两者之间切换。
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_adapter: LLMToolAdapter,
        skill_instructions: str = "",
        technical_skill_policy: str = "",
        max_steps: int = AGENT_MAX_STEPS_DEFAULT,
        mode: str = "standard",
        skill_manager=None,
        config=None,
    ):
        """注入共享依赖并搭建所选的多 Agent 流水线。"""
        self.tool_registry = tool_registry
        self.llm_adapter = llm_adapter
        self.skill_instructions = skill_instructions
        self.technical_skill_policy = technical_skill_policy
        self.max_steps = max_steps
        self.mode = mode if mode in VALID_MODES else "standard"
        self.skill_manager = skill_manager
        self.config = config

    def _get_timeout_seconds(self) -> int:
        """返回流水线超时秒数。

        ``0`` 表示禁用。该超时是整个流水线的协作式预算，而非对进行中阶段的
        硬性中断。
        """
        raw_value = getattr(self.config, "agent_orchestrator_timeout_s", 0)
        try:
            return max(0, int(raw_value or 0))
        except (TypeError, ValueError):
            return 0

    def _build_timeout_result(
        self,
        stats: AgentRunStats,
        all_tool_calls: List[Dict[str, Any]],
        models_used: List[str],
        elapsed_s: float,
        timeout_s: int,
        ctx: Optional[AgentContext] = None,
        parse_dashboard: bool = True,
    ) -> OrchestratorResult:
        """构建标准的超时结果载荷。"""
        stats.total_duration_s = round(elapsed_s, 2)
        stats.models_used = list(dict.fromkeys(models_used))
        error = f"Pipeline timed out after {elapsed_s:.2f}s (limit: {timeout_s}s)"
        provider = stats.models_used[0] if stats.models_used else ""
        model = ", ".join(stats.models_used)

        dashboard = None
        content = ""
        if ctx is not None:
            dashboard, content = self._resolve_final_output(ctx, parse_dashboard=parse_dashboard)
            if parse_dashboard and dashboard is not None:
                dashboard = self._mark_partial_dashboard(
                    dashboard,
                    note="多 Agent 超时，以下结论基于已完成阶段自动降级生成。",
                )
                ctx.set_data("final_dashboard", dashboard)
                content = json.dumps(dashboard, ensure_ascii=False, indent=2)

        return OrchestratorResult(
            success=bool(content) if (not parse_dashboard or dashboard is not None) else False,
            content=content,
            dashboard=dashboard,
            error=error,
            stats=stats,
            total_steps=stats.total_stages,
            total_tokens=stats.total_tokens,
            tool_calls_log=all_tool_calls,
            provider=provider,
            model=model,
        )

    def _build_budget_skip_result(
        self,
        stats: AgentRunStats,
        all_tool_calls: List[Dict[str, Any]],
        models_used: List[str],
        elapsed_s: float,
        timeout_s: int,
        stage_name: str,
        remaining_budget: float,
        min_stage_budget_s: int,
        ctx: Optional[AgentContext] = None,
        parse_dashboard: bool = True,
    ) -> OrchestratorResult:
        """为预算不足导致的阶段跳过构建结果（区别于超时语义）。"""
        stats.total_duration_s = round(elapsed_s, 2)
        stats.models_used = list(dict.fromkeys(models_used))
        dashboard = None
        content = ""
        if ctx is not None:
            dashboard, content = self._resolve_final_output(ctx, parse_dashboard=parse_dashboard)
            if parse_dashboard and dashboard is not None:
                dashboard = self._mark_partial_dashboard(
                    dashboard,
                    note="多 Agent 预算不足，以下结论基于已完成阶段自动降级生成。",
                )
                ctx.set_data("final_dashboard", dashboard)
                content = json.dumps(dashboard, ensure_ascii=False, indent=2)

        return OrchestratorResult(
            success=bool(content) if (not parse_dashboard or dashboard is not None) else False,
            content=content,
            dashboard=dashboard,
            error=(
                f"Pipeline skipped before stage '{stage_name}' due to insufficient budget "
                f"({remaining_budget:.1f}s remaining, minimum {min_stage_budget_s}s required)"
            ),
            stats=stats,
            total_steps=stats.total_stages,
            total_tokens=stats.total_tokens,
            tool_calls_log=all_tool_calls,
            provider=stats.models_used[0] if stats.models_used else "",
            model=", ".join(stats.models_used),
        )


    def _prepare_agent(self, agent: Any) -> Any:
        """把协调器级别的运行时设置应用到子 Agent。

        当协调器级 ``max_steps`` 等于默认值（``AGENT_MAX_STEPS_DEFAULT``）时，
        每个 Agent 保留各自的单 Agent 步数上限——这避免把原本设计为 3 步的
        决策 Agent 放大到 10 步。

        当用户**显式**把全局上限提高到默认值之上时，所有 Agent 采用该全局值，
        以尊重用户允许更多步数的意图。

        当用户把全局上限**降低**到某 Agent 默认值之下时，该 Agent 被全局值封顶。
        """
        if hasattr(agent, "max_steps"):
            if self.max_steps > AGENT_MAX_STEPS_DEFAULT:
                # 用户显式提高了上限——应用到所有 Agent。
                agent.max_steps = self.max_steps
            else:
                # 默认或降低——保留单 Agent 上限作为封顶值。
                agent.max_steps = min(agent.max_steps, self.max_steps)
        return agent

    def _callable_accepts_timeout_kwarg(self, func: Any) -> Optional[bool]:
        """当可被检视时，判断可调用对象是否接受 ``timeout_seconds`` 参数。"""
        if not callable(func):
            return None
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return None

        if "timeout_seconds" in signature.parameters:
            return True
        return any(
            param.kind is inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )

    def _agent_run_accepts_timeout(self, run_callable: Any) -> bool:
        """对旧版测试替身/自定义 Agent 做尽力而为的兼容性检查。"""
        side_effect = getattr(run_callable, "side_effect", None)
        accepts_timeout = self._callable_accepts_timeout_kwarg(side_effect)
        if accepts_timeout is not None:
            return accepts_timeout

        accepts_timeout = self._callable_accepts_timeout_kwarg(run_callable)
        if accepts_timeout is not None:
            return accepts_timeout

        return True

    def _run_stage_agent(
        self,
        agent: Any,
        ctx: AgentContext,
        progress_callback: Optional[Callable] = None,
        timeout_seconds: Optional[float] = None,
    ) -> StageResult:
        """运行阶段 Agent，同时兼容旧的调用签名。"""
        run_kwargs = {"progress_callback": progress_callback}
        if (
            timeout_seconds is not None
            and timeout_seconds > 0
            and self._agent_run_accepts_timeout(agent.run)
        ):
            run_kwargs["timeout_seconds"] = timeout_seconds
        return agent.run(ctx, **run_kwargs)

    # -----------------------------------------------------------------
    # 公共接口（镜像 AgentExecutor）
    # -----------------------------------------------------------------

    def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> "AgentResult":
        """运行多 Agent 流水线，生成仪表盘分析。

        返回 ``AgentResult``（与 ``AgentExecutor.run`` 类型一致）。
        """
        from src.agent.executor import AgentResult

        ctx = self._build_context(task, context)
        ctx.meta["response_mode"] = "dashboard"
        orch_result = self._execute_pipeline(ctx, parse_dashboard=True)

        return AgentResult(
            success=orch_result.success,
            content=orch_result.content,
            dashboard=orch_result.dashboard,
            tool_calls_log=orch_result.tool_calls_log,
            total_steps=orch_result.total_steps,
            total_tokens=orch_result.total_tokens,
            provider=orch_result.provider,
            model=orch_result.model,
            error=orch_result.error,
            skill_opinions=orch_result.skill_opinions,
        )

    def chat(
        self,
        message: str,
        session_id: str,
        progress_callback: Optional[Callable] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> "AgentResult":
        """以聊天模式运行流水线（自由回答，不做仪表盘解析）。

        对话历史由调用方（通过 ``conversation_manager``）在外部管理；
        协调器只专注于多 Agent 协同。
        """
        from src.agent.executor import AgentResult
        from src.agent.conversation import conversation_manager

        ctx = self._build_context(message, context)
        ctx.session_id = session_id
        ctx.meta["response_mode"] = "chat"

        session = conversation_manager.get_or_create(session_id)
        history = session.get_history()
        if history:
            ctx.meta["conversation_history"] = history

        # 持久化用户发言
        conversation_manager.add_message(session_id, "user", message)

        orch_result = self._execute_pipeline(
            ctx,
            parse_dashboard=False,
            progress_callback=progress_callback,
        )

        # 持久化助手回复
        if orch_result.success:
            conversation_manager.add_message(session_id, "assistant", orch_result.content)
        else:
            conversation_manager.add_message(
                session_id, "assistant",
                f"[分析失败] {orch_result.error or '未知错误'}",
            )

        return AgentResult(
            success=orch_result.success,
            content=orch_result.content,
            dashboard=orch_result.dashboard,
            tool_calls_log=orch_result.tool_calls_log,
            total_steps=orch_result.total_steps,
            total_tokens=orch_result.total_tokens,
            provider=orch_result.provider,
            model=orch_result.model,
            error=orch_result.error,
        )

    # -----------------------------------------------------------------
    # 流水线执行
    # -----------------------------------------------------------------

    def _execute_pipeline(
        self,
        ctx: AgentContext,
        parse_dashboard: bool = True,
        progress_callback: Optional[Callable] = None,
    ) -> OrchestratorResult:
        """根据 ``self.mode`` 运行 Agent 流水线。"""
        stats = AgentRunStats()
        all_tool_calls: List[Dict[str, Any]] = []
        models_used: List[str] = []
        t0 = time.time()
        timeout_s = self._get_timeout_seconds()

        agents = self._build_agent_chain(ctx)
        specialist_agents_inserted = False
        index = 0

        # 一个阶段做有效工作所需的最少秒数。若剩余预算低于该值仍启动阶段，
        # 几乎必然超时，白白浪费一次 LLM 计费。该限制只在至少完成一个阶段后
        # 才生效，从而保证第一阶段即使在总预算很小时也能获得运行机会。
        _MIN_STAGE_BUDGET_S = 15

        while index < len(agents):
            agent = agents[index]
            elapsed_s = time.time() - t0
            remaining_budget = timeout_s - elapsed_s if timeout_s else None
            stage_min_budget_s = (
                _MIN_STAGE_BUDGET_S
            )
            timeout_exhausted = (
                timeout_s
                and remaining_budget is not None
                and remaining_budget <= 0
            )
            budget_guard_triggered = (
                timeout_s
                and remaining_budget is not None
                and index > 0
                and remaining_budget < stage_min_budget_s
            )
            if timeout_exhausted:
                logger.error("[Orchestrator] pipeline timed out before stage '%s'", agent.agent_name)
                if progress_callback:
                    progress_callback({
                        "type": "pipeline_timeout",
                        "stage": agent.agent_name,
                        "elapsed": round(elapsed_s, 2),
                        "timeout": timeout_s,
                    })
                return self._build_timeout_result(
                    stats,
                    all_tool_calls,
                    models_used,
                    elapsed_s,
                    timeout_s,
                    ctx=ctx,
                    parse_dashboard=parse_dashboard,
                )

            if budget_guard_triggered:
                logger.warning(
                    "[Orchestrator] pipeline insufficient budget before stage '%s' (%.1fs remaining, min %ds)",
                    agent.agent_name,
                    remaining_budget,
                    stage_min_budget_s,
                )
                if progress_callback:
                    progress_callback({
                        "type": "pipeline_timeout",
                        "stage": agent.agent_name,
                        "elapsed": round(elapsed_s, 2),
                        "timeout": timeout_s,
                    })
                return self._build_budget_skip_result(
                    stats,
                    all_tool_calls,
                    models_used,
                    elapsed_s,
                    timeout_s,
                    agent.agent_name,
                    remaining_budget,
                    stage_min_budget_s,
                    ctx=ctx,
                    parse_dashboard=parse_dashboard,
                )

            # specialist 模式下，在决策阶段之前惰性插入专家 Agent
            if (
                self.mode == "specialist"
                and agent.agent_name == "decision"
                and not specialist_agents_inserted
            ):
                specialist_agents = self._build_specialist_agents(ctx)
                self._skill_agent_names = {a.agent_name for a in specialist_agents}
                specialist_agents_inserted = True
                if specialist_agents:
                    self._run_specialist_batch(
                        specialist_agents, ctx, stats, all_tool_calls, models_used,
                        progress_callback, timeout_s=max(0.0, timeout_s - elapsed_s) if timeout_s else None,
                    )

            # 决策 Agent 运行前先聚合各技能意见
            if agent.agent_name == "decision" and getattr(self, "_skill_agent_names", None):
                self._aggregate_skill_opinions(ctx)

            if progress_callback:
                progress_callback({
                    "type": "stage_start",
                    "stage": agent.agent_name,
                    "message": f"Starting {agent.agent_name} analysis...",
                })

            remaining_timeout_s = (
                max(0.0, timeout_s - elapsed_s)
                if timeout_s
                else None
            )
            result: StageResult = self._run_stage_agent(
                agent,
                ctx,
                progress_callback=progress_callback,
                timeout_seconds=remaining_timeout_s,
            )
            stats.record_stage(result)
            all_tool_calls.extend(
                tc for tc in (result.meta.get("tool_calls_log") or [])
            )
            models_used.extend(result.meta.get("models_used", []))

            elapsed_s = time.time() - t0
            if timeout_s and elapsed_s >= timeout_s:
                logger.error("[Orchestrator] pipeline timed out after stage '%s'", agent.agent_name)
                if progress_callback:
                    progress_callback({
                        "type": "pipeline_timeout",
                        "stage": agent.agent_name,
                        "elapsed": round(elapsed_s, 2),
                        "timeout": timeout_s,
                    })
                return self._build_timeout_result(
                    stats,
                    all_tool_calls,
                    models_used,
                    elapsed_s,
                    timeout_s,
                    ctx=ctx,
                    parse_dashboard=parse_dashboard,
                )

            if progress_callback:
                progress_callback({
                    "type": "stage_done",
                    "stage": agent.agent_name,
                    "status": result.status.value,
                    "duration": result.duration_s,
                })

            if ctx.meta.get("response_mode") == "chat" and agent.agent_name == "decision":
                final_text = result.meta.get("raw_text")
                if isinstance(final_text, str) and final_text.strip():
                    ctx.set_data("final_response_text", final_text.strip())

            if result.success and agent.agent_name == "decision":
                self._apply_risk_override(ctx)

            # 关键阶段失败时中止流水线。
            # 可优雅降级的非关键阶段：
            #   - intel / risk（标准支撑阶段）
            #   - 技能 Agent（专家评估，可选）
            if result.status == StageStatus.FAILED:
                non_critical = (
                    agent.agent_name in ("intel", "risk")
                    or agent.agent_name in getattr(self, "_skill_agent_names", set())
                )
                if not non_critical:
                    logger.error("[Orchestrator] critical stage '%s' failed: %s", agent.agent_name, result.error)
                    return OrchestratorResult(
                        success=False,
                        error=f"Stage '{agent.agent_name}' failed: {result.error}",
                        stats=stats,
                        total_tokens=stats.total_tokens,
                        tool_calls_log=all_tool_calls,
                    )
                else:
                    logger.warning("[Orchestrator] stage '%s' failed (non-critical, degrading): %s", agent.agent_name, result.error)

            index += 1

        # 组装最终输出
        total_duration = round(time.time() - t0, 2)
        stats.total_duration_s = total_duration
        stats.models_used = list(dict.fromkeys(models_used))

        dashboard, content = self._resolve_final_output(ctx, parse_dashboard=parse_dashboard)

        model_str = ", ".join(dict.fromkeys(m for m in models_used if m))
        provider = stats.models_used[0] if stats.models_used else ""

        if parse_dashboard and dashboard is None:
            return OrchestratorResult(
                success=False,
                content=content,
                dashboard=None,
                tool_calls_log=all_tool_calls,
                total_steps=stats.total_stages,
                total_tokens=stats.total_tokens,
                provider=provider,
                model=model_str,
                error="Failed to parse dashboard JSON from agent response",
                stats=stats,
            )

        return OrchestratorResult(
            success=bool(content),
            content=content,
            dashboard=dashboard,
            tool_calls_log=all_tool_calls,
            total_steps=stats.total_stages,
            total_tokens=stats.total_tokens,
            provider=provider,
            model=model_str,
            stats=stats,
            skill_opinions=[
                {"skill_id": extract_skill_id(op.agent_name) or op.agent_name, "signal": op.signal, "confidence": op.confidence, "observed_at": op.timestamp}
                for op in ctx.opinions if is_skill_agent_name(op.agent_name)
            ],
        )

    # -----------------------------------------------------------------
    # Agent 链构建
    # -----------------------------------------------------------------

    def _build_agent_chain(self, ctx: AgentContext) -> list:
        """根据 ``self.mode`` 实例化有序的 Agent 列表。"""
        from src.agent.agents.technical_agent import TechnicalAgent
        from src.agent.agents.intel_agent import IntelAgent
        from src.agent.agents.decision_agent import DecisionAgent
        from src.agent.agents.risk_agent import RiskAgent

        self._skill_agent_names = set()

        common_kwargs = dict(
            tool_registry=self.tool_registry,
            llm_adapter=self.llm_adapter,
            skill_instructions=self.skill_instructions,
            technical_skill_policy=self.technical_skill_policy,
        )

        technical = self._prepare_agent(TechnicalAgent(**common_kwargs))
        intel = self._prepare_agent(IntelAgent(**common_kwargs))
        risk = self._prepare_agent(RiskAgent(**common_kwargs))
        decision = self._prepare_agent(DecisionAgent(**common_kwargs))

        if self.mode == "quick":
            return [technical, decision]
        elif self.mode == "standard":
            return [technical, intel, decision]
        elif self.mode == "full":
            return [technical, intel, risk, decision]
        elif self.mode == "specialist":
            # 专家 Agent 在决策阶段前被惰性插入，好让路由器能读到已完成的技术意见。
            return [technical, intel, risk, decision]
        else:
            return [technical, intel, decision]

    def _build_specialist_agents(self, ctx: AgentContext) -> list:
        """根据请求的技能构建专家子 Agent。

        使用技能路由器选出适用技能，再为每个技能创建轻量 Agent 包装。
        """
        try:
            from src.agent.skills.router import SkillRouter
            common_kwargs = dict(
                tool_registry=self.tool_registry,
                llm_adapter=self.llm_adapter,
                skill_instructions=self.skill_instructions,
                technical_skill_policy=self.technical_skill_policy,
            )
            router = SkillRouter()
            selected = router.select_skills(ctx)
            if not selected:
                return []

            from src.agent.skills.skill_agent import SkillAgent
            agents = []
            for skill_id in selected[:3]:  # 最多 3 个并发技能
                agent = self._prepare_agent(SkillAgent(
                    skill_id=skill_id,
                    **common_kwargs,
                ))
                agents.append(agent)
            return agents
        except Exception as exc:
            logger.warning("[Orchestrator] failed to build skill agents: %s", exc)
            return []

    def _build_skill_agents(self, ctx: AgentContext) -> list:
        """为旧版导入保留的兼容包装。"""
        return self._build_specialist_agents(ctx)

    def _run_specialist_batch(self, agents, ctx, stats, all_tool_calls, models_used, progress_callback, *, timeout_s=None) -> None:
        """Execute isolated skill workers concurrently and merge their opinions in selected order."""
        from src.agent.skills.scheduler import run_concurrently
        def runner(agent, isolated_ctx):
            if progress_callback:
                progress_callback({"type": "stage_start", "stage": agent.agent_name, "message": f"Starting {agent.agent_name} analysis..."})
            return self._run_stage_agent(agent, isolated_ctx, progress_callback=progress_callback, timeout_seconds=timeout_s)
        for run in run_concurrently(
            agents,
            ctx,
            runner,
            max_workers=getattr(self.config, "agent_skill_max_concurrency", 3),
        ):
            stats.record_stage(run.stage)
            all_tool_calls.extend(run.stage.meta.get("tool_calls_log") or [])
            models_used.extend(run.stage.meta.get("models_used", []))
            for opinion in run.opinions:
                ctx.add_opinion(opinion)
            if progress_callback:
                progress_callback({"type": "stage_done", "stage": run.stage.stage_name, "status": run.stage.status.value, "duration": run.stage.duration_s})

    def _build_strategy_agents(self, ctx: AgentContext) -> list:
        """为旧版测试/导入保留的兼容包装。"""
        return self._build_specialist_agents(ctx)

    # -----------------------------------------------------------------
    # 技能聚合
    # -----------------------------------------------------------------

    def _aggregate_skill_opinions(self, ctx: AgentContext) -> None:
        """运行 SkillAggregator 生成共识意见。

        把各技能 Agent 的独立意见合并为单一加权共识，并存入上下文供决策 Agent 使用。
        """
        try:
            from src.agent.skills.aggregator import SkillAggregator
            aggregator = SkillAggregator()
            consensus = aggregator.aggregate(ctx)
            if consensus:
                from src.agent.skills.synthesis import synthesize
                synthesis = synthesize(
                    ctx.opinions,
                    weighted_score=(consensus.raw_data or {}).get("weighted_score"),
                    confidence=consensus.confidence,
                )
                consensus.raw_data = {
                    **(consensus.raw_data or {}),
                    "strategy_synthesis": synthesis,
                    "final_action": synthesis["final_action"],
                }
                consensus.signal = synthesis["final_signal"]
                consensus.confidence = synthesis["confidence"]
                ctx.add_opinion(consensus)
                ctx.set_data("skill_consensus", {
                    "signal": consensus.signal,
                    "confidence": consensus.confidence,
                    "reasoning": consensus.reasoning,
                    "final_action": synthesis["final_action"],
                    "synthesis": synthesis,
                })
                logger.info(
                    "[Orchestrator] skill consensus: signal=%s confidence=%.2f",
                    consensus.signal, consensus.confidence,
                )
            else:
                logger.info("[Orchestrator] no skill opinions to aggregate")
        except Exception as exc:
            logger.warning("[Orchestrator] skill aggregation failed: %s", exc)

    def _aggregate_strategy_opinions(self, ctx: AgentContext) -> None:
        """为旧版测试/导入保留的兼容包装。"""
        self._aggregate_skill_opinions(ctx)

    # -----------------------------------------------------------------
    # 辅助方法
    # -----------------------------------------------------------------

    def _build_context(self, task: str, context: Optional[Dict[str, Any]] = None) -> AgentContext:
        """根据用户请求初始化 ``AgentContext``。"""
        ctx = AgentContext(query=task)

        if context:
            ctx.stock_code = context.get("stock_code", "")
            ctx.stock_name = context.get("stock_name", "")
            requested_skills = context.get("skills")
            if requested_skills is None:
                requested_skills = context.get("strategies", [])
            ctx.meta["skills_requested"] = requested_skills or []
            ctx.meta["strategies_requested"] = requested_skills or []
            ctx.meta["report_language"] = normalize_report_language(context.get("report_language", "zh"))

            # 预填充调用方已经持有的数据字段
            for data_key in (
                "realtime_quote",
                "daily_history",
                "chip_distribution",
                "trend_result",
                "news_context",
                "fundamental_context",
                "stock_profile",
            ):
                if context.get(data_key):
                    ctx.set_data(data_key, context[data_key])

        # 尝试从查询文本中提取股票代码
        if not ctx.stock_code:
            ctx.stock_code = _extract_stock_code(task)

        if "report_language" not in ctx.meta:
            ctx.meta["report_language"] = "zh"

        return ctx

    @staticmethod
    def _fallback_summary(ctx: AgentContext) -> str:
        """当仪表盘 JSON 不可用时构建纯文本摘要。"""
        lines = [f"# Analysis Summary: {ctx.stock_code} ({ctx.stock_name})", ""]
        for op in ctx.opinions:
            lines.append(f"## {op.agent_name}")
            lines.append(f"Signal: {op.signal} (confidence: {op.confidence:.0%})")
            lines.append(op.reasoning)
            lines.append("")
        if ctx.risk_flags:
            lines.append("## Risk Flags")
            for rf in ctx.risk_flags:
                lines.append(f"- [{rf['severity']}] {rf['description']}")
        return "\n".join(lines)

    def _resolve_final_output(
        self,
        ctx: AgentContext,
        *,
        parse_dashboard: bool,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        """从上下文中解析出可用的最佳最终输出。

        仪表盘模式按以下优先级选择：
        1. 已解析/归一化的决策仪表盘
        2. 解析原始仪表盘文本
        3. 由已完成意见合成的仪表盘
        4. 纯文本兜底摘要
        """
        final_dashboard = ctx.get_data("final_dashboard")
        final_raw = ctx.get_data("final_dashboard_raw")
        final_text = ctx.get_data("final_response_text")
        chat_mode = ctx.meta.get("response_mode") == "chat"

        if parse_dashboard:
            dashboard = self._resolve_dashboard_payload(ctx, final_dashboard, final_raw)
            if dashboard is not None:
                return dashboard, json.dumps(dashboard, ensure_ascii=False, indent=2)
            if ctx.opinions:
                return None, self._fallback_summary(ctx)
            return None, ""

        if chat_mode and isinstance(final_text, str) and final_text.strip():
            return None, final_text.strip()
        if isinstance(final_raw, str) and final_raw.strip():
            return None, final_raw
        if isinstance(final_dashboard, dict):
            dashboard = self._normalize_dashboard_payload(final_dashboard, ctx)
            if dashboard is not None:
                return dashboard, json.dumps(dashboard, ensure_ascii=False, indent=2)
        if ctx.opinions:
            return None, self._fallback_summary(ctx)
        return None, ""

    def _resolve_dashboard_payload(
        self,
        ctx: AgentContext,
        final_dashboard: Any,
        final_raw: Any,
    ) -> Optional[Dict[str, Any]]:
        """返回归一化仪表盘，或从部分上下文合成一个。"""
        dashboard: Optional[Dict[str, Any]] = None

        if isinstance(final_dashboard, dict):
            dashboard = self._normalize_dashboard_payload(final_dashboard, ctx)
        elif isinstance(final_raw, str) and final_raw.strip():
            parsed = parse_dashboard_json(final_raw)
            if isinstance(parsed, dict):
                dashboard = self._normalize_dashboard_payload(parsed, ctx)

        if dashboard is None:
            dashboard = self._normalize_dashboard_payload({}, ctx)

        if dashboard is None:
            return None

        ctx.set_data("final_dashboard", dashboard)
        # 应用风控覆盖（幂等——即使在 _execute_pipeline 决策阶段后已应用过，
        # 再次调用也是安全的）。
        self._apply_risk_override(ctx)
        overridden = ctx.get_data("final_dashboard")
        if isinstance(overridden, dict):
            return overridden
        return dashboard

    def _normalize_dashboard_payload(
        self,
        payload: Optional[Dict[str, Any]],
        ctx: AgentContext,
    ) -> Optional[Dict[str, Any]]:
        """归一化或合成下游期望的仪表盘结构。"""
        payload = dict(payload or {})
        meaningful_data_keys = (
            "realtime_quote",
            "daily_history",
            "chip_distribution",
            "trend_result",
            "news_context",
            "intel_opinion",
            "fundamental_context",
        )
        has_meaningful_context = any(ctx.get_data(key) is not None for key in meaningful_data_keys)
        if not payload and not ctx.opinions and not has_meaningful_context:
            return None

        base_opinion = self._select_base_opinion(ctx)
        decision_type = normalize_decision_signal(
            payload.get("decision_type") or (base_opinion.signal if base_opinion else "hold")
        )
        confidence = float(base_opinion.confidence if base_opinion is not None else 0.5)
        sentiment_score = payload.get("sentiment_score")
        try:
            sentiment_score = int(sentiment_score)
        except (TypeError, ValueError):
            sentiment_score = _estimate_sentiment_score(decision_type, confidence)

        dashboard_block = payload.get("dashboard")
        if not isinstance(dashboard_block, dict):
            dashboard_block = {}
        else:
            dashboard_block = dict(dashboard_block)

        core = dashboard_block.get("core_conclusion")
        if not isinstance(core, dict):
            core = {}
        else:
            core = dict(core)

        intelligence = dashboard_block.get("intelligence")
        if not isinstance(intelligence, dict):
            intelligence = {}
        else:
            intelligence = dict(intelligence)

        battle = dashboard_block.get("battle_plan")
        if not isinstance(battle, dict):
            battle = {}
        else:
            battle = dict(battle)

        analysis_summary = _first_non_empty_text(
            payload.get("analysis_summary"),
            core.get("one_sentence"),
            getattr(base_opinion, "reasoning", ""),
        )
        if not analysis_summary:
            analysis_summary = f"多 Agent 未生成完整仪表盘，当前按{_signal_to_operation(decision_type)}处理。"
        analysis_summary = _truncate_text(analysis_summary, 220)

        trend_prediction = _first_non_empty_text(
            payload.get("trend_prediction"),
            (getattr(base_opinion, "raw_data", {}) or {}).get("trend_summary")
            if base_opinion is not None else "",
        )
        if not trend_prediction:
            technical = self._latest_opinion(ctx, {"technical"})
            tech_raw = technical.raw_data if technical and isinstance(technical.raw_data, dict) else {}
            ma_alignment = tech_raw.get("ma_alignment")
            trend_score = tech_raw.get("trend_score")
            if ma_alignment or trend_score is not None:
                trend_prediction = f"技术面{ma_alignment or 'neutral'}，趋势评分 {trend_score if trend_score is not None else 'N/A'}"
            else:
                trend_prediction = "待结合更多阶段结果确认"

        operation_advice_raw = payload.get("operation_advice")
        operation_advice = _normalize_operation_advice_value(operation_advice_raw, decision_type)

        existing_position = core.get("position_advice")
        position_advice = dict(existing_position) if isinstance(existing_position, dict) else {}
        if isinstance(operation_advice_raw, dict):
            no_position = _first_non_empty_text(
                operation_advice_raw.get("no_position"),
                operation_advice_raw.get("empty_position"),
            )
            has_position = _first_non_empty_text(
                operation_advice_raw.get("has_position"),
                operation_advice_raw.get("holding_position"),
            )
            if no_position and "no_position" not in position_advice:
                position_advice["no_position"] = no_position
            if has_position and "has_position" not in position_advice:
                position_advice["has_position"] = has_position
        defaults = _default_position_advice(decision_type)
        position_advice.setdefault("no_position", defaults["no_position"])
        position_advice.setdefault("has_position", defaults["has_position"])

        key_levels = self._collect_key_levels(ctx, payload, dashboard_block)
        sniper = battle.get("sniper_points")
        if not isinstance(sniper, dict):
            sniper = {}
        else:
            sniper = dict(sniper)

        ideal_buy = _pick_first_level(
            sniper.get("ideal_buy"),
            key_levels.get("ideal_buy_if_valuation_improves"),
            key_levels.get("ideal_buy"),
            key_levels.get("support"),
            key_levels.get("immediate_support"),
        )
        sniper["ideal_buy"] = ideal_buy if ideal_buy is not None else "N/A"

        secondary_buy = _coerce_level_value(sniper.get("secondary_buy"))
        if secondary_buy is None:
            secondary_buy = _pick_first_level(
                key_levels.get("secondary_buy"),
                key_levels.get("support"),
                key_levels.get("immediate_support"),
            )
        if _level_values_equal(secondary_buy, sniper.get("ideal_buy")):
            secondary_buy = None
        sniper["secondary_buy"] = secondary_buy if secondary_buy is not None else "N/A"
        sniper.setdefault(
            "stop_loss",
            key_levels.get("stop_loss")
            or key_levels.get("strong_support_stop_loss")
            or "待补充",
        )
        sniper.setdefault(
            "take_profit",
            key_levels.get("take_profit")
            or key_levels.get("next_breakout_target")
            or key_levels.get("current_resistance")
            or key_levels.get("resistance")
            or "N/A",
        )

        risk_alerts = self._collect_risk_alerts(ctx, intelligence)
        positive_catalysts = self._collect_positive_catalysts(ctx, intelligence)
        latest_news = _extract_latest_news_title(intelligence)

        if not intelligence.get("risk_alerts"):
            intelligence["risk_alerts"] = risk_alerts
        if positive_catalysts and not intelligence.get("positive_catalysts"):
            intelligence["positive_catalysts"] = positive_catalysts
        if latest_news and not intelligence.get("latest_news"):
            intelligence["latest_news"] = latest_news

        if not core.get("one_sentence"):
            core["one_sentence"] = _truncate_text(analysis_summary, 60)
        if not core.get("time_sensitivity"):
            core["time_sensitivity"] = "本周内"
        if not core.get("signal_type"):
            core["signal_type"] = _signal_to_signal_type(decision_type)
        core["position_advice"] = position_advice

        battle["sniper_points"] = sniper
        if "action_checklist" not in battle:
            battle["action_checklist"] = []
        position_strategy = battle.get("position_strategy")
        if not isinstance(position_strategy, dict) or not position_strategy:
            battle["position_strategy"] = {
                "suggested_position": _default_position_size(decision_type),
                "entry_plan": position_advice["no_position"],
                "risk_control": f"止损参考 {sniper.get('stop_loss', '待补充')}",
            }

        data_perspective = dashboard_block.get("data_perspective")
        if not isinstance(data_perspective, dict):
            data_perspective = {}
        if not data_perspective:
            built_data_perspective = self._build_data_perspective(ctx, key_levels)
            if built_data_perspective:
                data_perspective = built_data_perspective
        if data_perspective:
            dashboard_block["data_perspective"] = data_perspective

        dashboard_block["core_conclusion"] = core
        dashboard_block["intelligence"] = intelligence
        dashboard_block["battle_plan"] = battle

        key_points = payload.get("key_points")
        if not isinstance(key_points, list) or not key_points:
            key_points = [
                _truncate_text(op.reasoning, 120)
                for op in ctx.opinions
                if isinstance(op.reasoning, str) and op.reasoning.strip()
            ][:5]

        risk_warning = _first_non_empty_text(
            payload.get("risk_warning"),
            "；".join(risk_alerts[:3]),
            getattr(self._latest_opinion(ctx, {"risk"}), "reasoning", ""),
        )
        if not risk_warning:
            risk_warning = "暂无额外风险提示"

        payload["stock_name"] = _first_non_empty_text(payload.get("stock_name"), ctx.stock_name, ctx.stock_code)
        payload["sentiment_score"] = sentiment_score
        payload["trend_prediction"] = trend_prediction
        payload["operation_advice"] = operation_advice
        payload["decision_type"] = decision_type
        payload["confidence_level"] = _confidence_label(confidence)
        payload["analysis_summary"] = analysis_summary
        payload["key_points"] = key_points
        payload["risk_warning"] = risk_warning
        payload["dashboard"] = dashboard_block
        return payload

    def _collect_key_levels(
        self,
        ctx: AgentContext,
        payload: Dict[str, Any],
        dashboard_block: Dict[str, Any],
    ) -> Dict[str, Any]:
        """从仪表盘载荷与各 Agent 意见中收集关键价位。"""
        levels: Dict[str, Any] = {}

        def absorb(source: Any) -> None:
            """从一个可能的来源合并归一化价位。"""
            if not isinstance(source, dict):
                return
            for key, value in source.items():
                normalized = _coerce_level_value(value)
                if normalized is not None and key not in levels:
                    levels[key] = normalized

        absorb(payload.get("key_levels"))
        absorb(dashboard_block.get("key_levels"))
        for opinion in reversed(ctx.opinions):
            absorb(getattr(opinion, "key_levels", {}))
            raw = opinion.raw_data if isinstance(opinion.raw_data, dict) else {}
            absorb(raw.get("key_levels"))
        return levels

    def _build_data_perspective(
        self,
        ctx: AgentContext,
        key_levels: Dict[str, Any],
    ) -> Dict[str, Any]:
        """从缓存的市场数据构建轻量 data_perspective 块。"""
        realtime = ctx.get_data("realtime_quote")
        chip = ctx.get_data("chip_distribution")
        trend = ctx.get_data("trend_result")
        technical = self._latest_opinion(ctx, {"technical"})
        tech_raw = technical.raw_data if technical and isinstance(technical.raw_data, dict) else {}
        trend_dict = trend if isinstance(trend, dict) else {}

        data_perspective: Dict[str, Any] = {}
        ma_alignment = tech_raw.get("ma_alignment")
        trend_score = tech_raw.get("trend_score")
        if ma_alignment or trend_score is not None:
            data_perspective["trend_status"] = {
                "ma_alignment": ma_alignment or "N/A",
                "trend_score": trend_score if trend_score is not None else "N/A",
                "is_bullish": str(ma_alignment).lower() == "bullish",
            }

        def _bias_label(bias):
            """把均线乖离率映射为紧凑的中文展示标签。"""
            if not isinstance(bias, (int, float)):
                return ""
            if bias > 5:
                return "超买"
            elif bias > 2:
                return "偏高"
            elif bias < -5:
                return "超卖"
            elif bias < -2:
                return "偏低"
            return "中性"

        def _r(val, n=2):
            """对数值做展示用四舍五入。"""
            return round(val, n) if isinstance(val, (int, float)) else val

        def _pick(primary_dict, primary_key, fallback_dict, fallback_key, default="N/A"):
            """取第一个非 None 值，避免落入假值 0 的陷阱。"""
            v = primary_dict.get(primary_key)
            if v is not None:
                return v
            v2 = fallback_dict.get(fallback_key, default)
            return v2 if v2 is not None else default

        if isinstance(realtime, dict) or trend_dict:
            data_perspective["price_position"] = {
                "current_price": _r(_pick(trend_dict, "current_price", realtime or {}, "price")),
                "ma5": _r(_pick(trend_dict, "ma5", tech_raw, "ma5")),
                "ma10": _r(_pick(trend_dict, "ma10", tech_raw, "ma10")),
                "ma20": _r(_pick(trend_dict, "ma20", tech_raw, "ma20")),
                "bias_ma5": _r(_pick(trend_dict, "bias_ma5", tech_raw, "bias_ma5")),
                "bias_status": _bias_label(trend_dict.get("bias_ma5")) or tech_raw.get("bias_status", "N/A"),
                "support_level": key_levels.get("support") or key_levels.get("immediate_support") or "N/A",
                "resistance_level": key_levels.get("resistance") or key_levels.get("current_resistance") or "N/A",
            }
            data_perspective["volume_analysis"] = {
                "volume_ratio": (realtime or {}).get("volume_ratio", "N/A"),
                "turnover_rate": (realtime or {}).get("turnover_rate", "N/A"),
                "volume_status": trend_dict.get("volume_status") or tech_raw.get("volume_status", "N/A"),
                "volume_meaning": tech_raw.get("reasoning", "") if tech_raw else "",
            }

        if isinstance(chip, dict):
            concentration = chip.get("concentration_90")
            if concentration is None:
                concentration = chip.get("concentration")
            data_perspective["chip_structure"] = {
                "profit_ratio": chip.get("profit_ratio", "N/A"),
                "avg_cost": chip.get("avg_cost", "N/A"),
                "concentration": concentration if concentration is not None else "N/A",
                "chip_health": chip.get("chip_health", "一般"),
            }

        return data_perspective

    def _collect_risk_alerts(
        self,
        ctx: AgentContext,
        intelligence: Dict[str, Any],
    ) -> List[str]:
        """从仪表盘载荷、intel/risk 意见和上下文收集风险警报。"""
        alerts: List[str] = []

        def absorb(values: Any) -> None:
            """从字符串/字典列表值中追加去重后的警报描述。"""
            if not isinstance(values, list):
                return
            for item in values:
                text = ""
                if isinstance(item, str):
                    text = item.strip()
                elif isinstance(item, dict):
                    text = str(item.get("description") or item.get("title") or "").strip()
                if text and text not in alerts:
                    alerts.append(text)

        absorb(intelligence.get("risk_alerts"))
        intel = self._latest_opinion(ctx, {"intel"})
        intel_raw = intel.raw_data if intel and isinstance(intel.raw_data, dict) else {}
        absorb(intel_raw.get("risk_alerts"))
        risk = self._latest_opinion(ctx, {"risk"})
        risk_raw = risk.raw_data if risk and isinstance(risk.raw_data, dict) else {}
        absorb(risk_raw.get("flags"))
        for flag in ctx.risk_flags:
            description = str(flag.get("description", "")).strip()
            if description and description not in alerts:
                alerts.append(description)
        return alerts[:8]

    def _collect_positive_catalysts(
        self,
        ctx: AgentContext,
        intelligence: Dict[str, Any],
    ) -> List[str]:
        """从仪表盘与 intel 意见载荷中收集去重后的积极催化剂。"""
        catalysts: List[str] = []

        def absorb(values: Any) -> None:
            """从列表载荷中追加去重后的催化剂文本。"""
            if not isinstance(values, list):
                return
            for item in values:
                text = str(item).strip()
                if text and text not in catalysts:
                    catalysts.append(text)

        absorb(intelligence.get("positive_catalysts"))
        intel = self._latest_opinion(ctx, {"intel"})
        intel_raw = intel.raw_data if intel and isinstance(intel.raw_data, dict) else {}
        absorb(intel_raw.get("positive_catalysts"))
        return catalysts[:8]

    @staticmethod
    def _latest_opinion(ctx: AgentContext, names: set[str]) -> Optional[Any]:
        """返回 agent_name 属于 names 的最新一条意见。"""
        for opinion in reversed(ctx.opinions):
            if opinion.agent_name in names:
                return opinion
        return None

    def _select_base_opinion(self, ctx: AgentContext) -> Optional[Any]:
        """选择最佳意见作为兜底仪表盘字段的锚点。"""
        preferred_groups = (
            {"decision"},
            {"skill_consensus", "strategy_consensus"},
            {"technical"},
            {"intel"},
            {"risk"},
        )
        for names in preferred_groups:
            opinion = self._latest_opinion(ctx, names)
            if opinion is not None:
                return opinion
        if ctx.opinions:
            return ctx.opinions[-1]
        return None

    @staticmethod
    def _mark_partial_dashboard(
        dashboard: Dict[str, Any],
        *,
        note: str,
    ) -> Dict[str, Any]:
        """给兜底仪表盘打标记，让调用方能看出它是部分降级生成的。"""
        tagged = dict(dashboard)
        summary = _first_non_empty_text(tagged.get("analysis_summary"))
        prefix = "[降级结果] "
        if summary and not summary.startswith(prefix):
            tagged["analysis_summary"] = prefix + summary
        elif not summary:
            tagged["analysis_summary"] = prefix + note

        warning = _first_non_empty_text(tagged.get("risk_warning"))
        tagged["risk_warning"] = f"{note} {warning}".strip() if warning else note

        nested = tagged.get("dashboard")
        if isinstance(nested, dict):
            nested = dict(nested)
            core = nested.get("core_conclusion")
            if isinstance(core, dict):
                core = dict(core)
                one_sentence = _first_non_empty_text(core.get("one_sentence"), tagged.get("analysis_summary"))
                if one_sentence and not str(one_sentence).startswith(prefix):
                    core["one_sentence"] = prefix + str(one_sentence)
                nested["core_conclusion"] = core
            tagged["dashboard"] = nested
        return tagged

    def _apply_risk_override(self, ctx: AgentContext) -> None:
        """把风险 Agent 的否决/降级规则应用到最终仪表盘。

        幂等：本次流水线运行中已应用过则跳过。
        """
        if ctx.get_data("risk_override_applied"):
            return

        if not getattr(self.config, "agent_risk_override", True):
            return

        dashboard = ctx.get_data("final_dashboard")
        if not isinstance(dashboard, dict):
            return

        risk_opinion = next((op for op in reversed(ctx.opinions) if op.agent_name == "risk"), None)
        risk_raw = risk_opinion.raw_data if risk_opinion and isinstance(risk_opinion.raw_data, dict) else {}

        adjustment = str(risk_raw.get("signal_adjustment") or "").lower()
        has_high_flag = any(str(flag.get("severity", "")).lower() == "high" for flag in ctx.risk_flags)
        veto_buy = bool(risk_raw.get("veto_buy")) or adjustment == "veto" or has_high_flag

        current_signal = normalize_decision_signal(dashboard.get("decision_type", "hold"))
        new_signal = current_signal
        if veto_buy and current_signal == "buy":
            new_signal = "hold"
        elif adjustment == "downgrade_one":
            new_signal = _downgrade_signal(current_signal, steps=1)
        elif adjustment == "downgrade_two":
            new_signal = _downgrade_signal(current_signal, steps=2)

        if new_signal == current_signal:
            return

        dashboard["decision_type"] = new_signal
        dashboard["risk_warning"] = self._merge_risk_warning(
            dashboard.get("risk_warning"),
            risk_raw,
            ctx.risk_flags,
            new_signal,
        )

        sentiment_score = dashboard.get("sentiment_score")
        try:
            score = int(sentiment_score)
        except (TypeError, ValueError):
            score = 50
        dashboard["sentiment_score"] = _adjust_sentiment_score(score, new_signal)

        operation_advice = dashboard.get("operation_advice")
        if isinstance(operation_advice, str):
            dashboard["operation_advice"] = _adjust_operation_advice(operation_advice, new_signal)

        summary = dashboard.get("analysis_summary")
        if isinstance(summary, str) and summary:
            dashboard["analysis_summary"] = f"[风控下调: {current_signal} -> {new_signal}] {summary}"

        dashboard_block = dashboard.get("dashboard")
        if isinstance(dashboard_block, dict):
            core = dashboard_block.get("core_conclusion")
            if isinstance(core, dict):
                signal_type = {
                    "buy": "🟡持有观望",
                    "hold": "🟡持有观望",
                    "sell": "🔴卖出信号",
                }.get(new_signal, "⚠️风险警告")
                core["signal_type"] = signal_type
                sentence = core.get("one_sentence")
                if isinstance(sentence, str) and sentence:
                    core["one_sentence"] = f"{sentence}（风控下调）"
                position = core.get("position_advice")
                if isinstance(position, dict):
                    if new_signal == "hold":
                        position["no_position"] = "风险未解除前先观望，等待更清晰的入场条件。"
                        position["has_position"] = "谨慎持有并收紧止损，待风险缓解后再考虑加仓。"
                    elif new_signal == "sell":
                        position["no_position"] = "风险明显偏高，暂不新开仓。"
                        position["has_position"] = "优先控制回撤，建议减仓或退出高风险仓位。"

        ctx.set_data("final_dashboard", dashboard)
        ctx.set_data("risk_override_applied", {
            "from": current_signal,
            "to": new_signal,
            "adjustment": adjustment or ("veto" if veto_buy else "none"),
        })

        for opinion in reversed(ctx.opinions):
            if opinion.agent_name == "decision":
                opinion.signal = new_signal
                if isinstance(dashboard.get("analysis_summary"), str):
                    opinion.reasoning = dashboard["analysis_summary"]
                opinion.raw_data = dashboard
                break

        logger.info(
            "[Orchestrator] risk override applied: %s -> %s (adjustment=%s, high_flag=%s)",
            current_signal,
            new_signal,
            adjustment or ("veto" if veto_buy else "none"),
            has_high_flag,
        )

    @staticmethod
    def _merge_risk_warning(
        existing_warning: Any,
        risk_raw: Dict[str, Any],
        risk_flags: List[Dict[str, Any]],
        signal: str,
    ) -> str:
        """在强制降级后构建简洁的风险警告。"""
        warnings: List[str] = []
        if isinstance(existing_warning, str) and existing_warning.strip():
            warnings.append(existing_warning.strip())
        if isinstance(risk_raw.get("reasoning"), str) and risk_raw["reasoning"].strip():
            warnings.append(risk_raw["reasoning"].strip())
        for flag in risk_flags[:3]:
            description = str(flag.get("description", "")).strip()
            severity = str(flag.get("severity", "")).lower()
            if description:
                warnings.append(f"[{severity or 'risk'}] {description}")
        prefix = f"风控接管：最终信号已下调为 {signal}。"
        merged = " ".join(dict.fromkeys([prefix] + warnings))
        return merged[:500]


# 不应被当作美股代码识别的常见英文词（2-5 个大写字母）。
# 由 _extract_stock_code() 检查；保留在模块级以避免每次调用时重建。
_COMMON_WORDS: set[str] = {
    # 代词 / 冠词 / 介词 / 连词
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL",
    "CAN", "HAD", "HER", "WAS", "ONE", "OUR", "OUT", "HAS",
    "HIS", "HOW", "ITS", "LET", "MAY", "NEW", "NOW", "OLD",
    "SEE", "WAY", "WHO", "DID", "GET", "HIM", "USE", "SAY",
    "SHE", "TOO", "ANY", "WITH", "FROM", "THAT", "THAN",
    "THIS", "WHAT", "WHEN", "WILL", "JUST", "ALSO",
    "BEEN", "EACH", "HAVE", "MUCH", "ONLY", "OVER",
    "SOME", "SUCH", "THEM", "THEN", "THEY", "VERY",
    "WERE", "YOUR", "ABOUT", "AFTER", "COULD", "EVERY",
    "OTHER", "THEIR", "THERE", "THESE", "THOSE", "WHICH",
    "WOULD", "BEING", "STILL", "WHERE",
    # 看起来像股票代码的金融/分析术语
    "BUY", "SELL", "HOLD", "LONG", "PUT", "CALL",
    "ETF", "IPO", "RSI", "EPS", "PEG", "ROE", "ROA",
    "USA", "USD", "CNY", "HKD", "EUR", "GBP",
    "STOCK", "TRADE", "PRICE", "INDEX", "FUND",
    "HIGH", "LOW", "OPEN", "CLOSE", "STOP", "LOSS",
    "TREND", "BULL", "BEAR", "RISK", "CASH", "BOND",
    "MACD", "VWAP", "BOLL",
    # 聊天消息中常见的问候/填充词
    "HELLO", "PLEASE", "THANKS", "CHECK", "LOOK", "THINK",
    "MAYBE", "GUESS", "TELL", "SHOW", "WHAT", "WHATS",
    "WHY", "WHEN", "HOWDY", "HEY", "HI",
}

_LOWERCASE_TICKER_HINTS = re.compile(
    r"分析|看看|查一?下|研究|诊断|走势|趋势|股价|股票|个股",
)


def _extract_stock_code(text: str) -> str:
    """从自由文本中尽力抽取股票代码。

    依次尝试：A 股 6 位数字 → 港股 hk 开头 → 美股 2-5 个大写字母。
    """
    # A 股 6 位数字：使用 lookahead/lookbehind 而非 \b，
    # 因为 Python 的 \b 不会在中文/数字边界触发。
    m = re.search(r'(?<!\d)((?:[03648]\d{5}|92\d{4}))(?!\d)', text)
    if m:
        return m.group(1)
    # 港股代码采用相同的 lookaround 思路
    m = re.search(r'(?<![a-zA-Z])(hk\d{5})(?!\d)', text, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # 美股代码：要求 2+ 个大写字母，且两侧是非字母字符。
    m = re.search(r'(?<![a-zA-Z])([A-Z]{2,5}(?:\.[A-Z]{1,2})?)(?![a-zA-Z])', text)
    if m:
        candidate = m.group(1)
        if candidate not in _COMMON_WORDS:
            return candidate

    stripped = (text or "").strip()
    bare_match = re.fullmatch(r'([A-Za-z]{2,5}(?:\.[A-Za-z]{1,2})?)', stripped)
    if bare_match:
        candidate = bare_match.group(1).upper()
        if candidate not in _COMMON_WORDS:
            return candidate

    if not _LOWERCASE_TICKER_HINTS.search(stripped):
        return ""

    for match in re.finditer(r'(?<![a-zA-Z])([A-Za-z]{2,5}(?:\.[A-Za-z]{1,2})?)(?![a-zA-Z])', text):
        raw_candidate = match.group(1)
        candidate = raw_candidate.upper()
        if candidate in _COMMON_WORDS:
            continue
        return candidate
    return ""


def _downgrade_signal(signal: str, steps: int = 1) -> str:
    """把看板决策信号下调一个或多个级别（buy→hold→sell）。"""
    order = ["buy", "hold", "sell"]
    try:
        index = order.index(signal)
    except ValueError:
        return signal
    return order[min(len(order) - 1, index + max(0, steps))]


def _adjust_sentiment_score(score: int, signal: str) -> int:
    """把情绪分夹逼到与覆盖后信号匹配的目标区间。"""
    bands = {
        "buy": (60, 79),
        "hold": (40, 59),
        "sell": (0, 39),
    }
    low, high = bands.get(signal, (0, 100))
    return max(low, min(high, score))


def _adjust_operation_advice(advice: str, signal: str) -> str:
    """把操作建议措辞归一化为与覆盖后的决策信号一致。"""
    mapping = {
        "buy": "买入",
        "hold": "观望",
        "sell": "减仓/卖出",
    }
    if signal not in mapping:
        return advice
    if advice == mapping[signal]:
        return advice
    return f"{mapping[signal]}（原建议已被风控下调）"


def _signal_to_operation(signal: str) -> str:
    """把标准决策信号映射为中文操作标签。"""
    mapping = {
        "buy": "买入",
        "hold": "观望",
        "sell": "减仓/卖出",
    }
    return mapping.get(signal, "观望")


def _signal_to_signal_type(signal: str) -> str:
    """把标准决策信号映射为看板信号徽标文本。"""
    mapping = {
        "buy": "🟢买入信号",
        "hold": "⚪观望信号",
        "sell": "🔴卖出信号",
    }
    return mapping.get(signal, "⚪观望信号")


def _default_position_advice(signal: str) -> Dict[str, str]:
    """为空看板载荷返回默认持仓建议（区分空仓/持仓两种场景）。"""
    mapping = {
        "buy": {
            "no_position": "可结合支撑位分批试仓，避免一次性追高。",
            "has_position": "可继续持有，回踩关键位不破再考虑加仓。",
        },
        "hold": {
            "no_position": "暂不追高，等待更清晰的入场条件。",
            "has_position": "以观察为主，跌破止损位再执行风控。",
        },
        "sell": {
            "no_position": "暂不参与，等待风险充分释放。",
            "has_position": "优先控制回撤，按计划减仓或离场。",
        },
    }
    return mapping.get(signal, mapping["hold"])


def _default_position_size(signal: str) -> str:
    """为作战计划返回默认仓位描述文本。"""
    mapping = {
        "buy": "轻仓试仓",
        "hold": "控制仓位",
        "sell": "降仓防守",
    }
    return mapping.get(signal, "控制仓位")


def _normalize_operation_advice_value(value: Any, signal: str) -> str:
    """有显式操作建议时直接采用，否则由信号推导。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _signal_to_operation(signal)


def _confidence_label(confidence: float) -> str:
    """Convert numeric confidence into the report's 高/中/低 label."""
    if confidence >= 0.75:
        return "高"
    if confidence >= 0.45:
        return "中"
    return "低"


def _estimate_sentiment_score(signal: str, confidence: float) -> int:
    """由标准信号与置信度估算 0-100 的情绪分。"""
    confidence = max(0.0, min(1.0, float(confidence)))
    bands = {
        "buy": (65, 79),
        "hold": (45, 59),
        "sell": (20, 39),
    }
    low, high = bands.get(signal, (45, 59))
    return int(round(low + (high - low) * confidence))


def _coerce_level_value(value: Any) -> Any:
    """归一化数值型价格点位，同时保留有意义的非数值文本。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).replace(",", "").replace("，", "").strip()
    if not text or text.upper() == "N/A" or text in {"-", "—"}:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return text


def _pick_first_level(*values: Any) -> Any:
    """返回第一个可被归一化为价格点位的值。"""
    for value in values:
        normalized = _coerce_level_value(value)
        if normalized is not None:
            return normalized
    return None


def _level_values_equal(left: Any, right: Any) -> bool:
    """归一化后比较两个价格点位是否相等。"""
    left_normalized = _coerce_level_value(left)
    right_normalized = _coerce_level_value(right)
    return (
        left_normalized is not None
        and right_normalized is not None
        and left_normalized == right_normalized
    )


def _first_non_empty_text(*values: Any) -> str:
    """从一组回退值中返回第一个非空字符串。"""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _truncate_text(text: Any, limit: int) -> str:
    """把文本裁剪到展示上限，超长时以省略号结尾。"""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _extract_latest_news_title(intelligence: Dict[str, Any]) -> str:
    """从 intelligence 载荷中提取一条简短的最新闻标题。"""
    key_news = intelligence.get("key_news")
    if isinstance(key_news, list):
        for item in key_news:
            if isinstance(item, dict):
                title = str(item.get("title", "")).strip()
                if title:
                    return title
    latest_news = intelligence.get("latest_news")
    if isinstance(latest_news, str) and latest_news.strip():
        return latest_news.strip()
    return ""
