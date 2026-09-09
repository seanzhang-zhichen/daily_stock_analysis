"""Concurrent, isolated execution for independent specialist skill agents."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from src.agent.protocols import AgentContext, AgentOpinion, StageResult

MAX_SKILL_WORKERS = 4


@dataclass
class SkillRunResult:
    """One specialist stage plus only the opinions it produced."""

    stage: StageResult
    opinions: list[AgentOpinion]


def run_concurrently(agents: list[Any], ctx: AgentContext, runner: Callable[[Any, AgentContext], StageResult], *, max_workers: int = 3) -> list[SkillRunResult]:
    """Run skills concurrently and return results in the original routing order."""
    if not agents:
        return []

    worker_count = max(1, min(int(max_workers or 1), len(agents), MAX_SKILL_WORKERS))
    results: dict[int, SkillRunResult] = {}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="agent-skill") as pool:
        futures = {
            pool.submit(copy_context().run, _run_one, agent, ctx, runner): index
            for index, agent in enumerate(agents)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [results[index] for index in range(len(agents))]


def _run_one(agent: Any, source: AgentContext, runner: Callable[[Any, AgentContext], StageResult]) -> SkillRunResult:
    isolated = _clone_context(source)
    existing_opinion_count = len(isolated.opinions)
    stage = runner(agent, isolated)
    opinions = [stage.opinion] if stage.opinion is not None else isolated.opinions[existing_opinion_count:]
    return SkillRunResult(stage=stage, opinions=[opinion for opinion in opinions if opinion is not None])


def _clone_context(source: AgentContext) -> AgentContext:
    """Clone mutable payloads so a specialist worker cannot mutate shared state."""
    return AgentContext(
        query=source.query,
        stock_code=source.stock_code,
        stock_name=source.stock_name,
        session_id=source.session_id,
        data=deepcopy(source.data),
        opinions=list(source.opinions),
        risk_flags=deepcopy(source.risk_flags),
        meta=deepcopy(source.meta),
        created_at=source.created_at,
    )
