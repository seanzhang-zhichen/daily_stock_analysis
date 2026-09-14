# -*- coding: utf-8 -*-
"""
技能调度器 —— 为独立专家技能 Agent 提供并发、隔离的执行环境。

通过线程池并发运行多个 SkillAgent，每个 Agent 在独立的上下文中执行，
避免共享状态被并发修改。同时提供上下文克隆机制，确保原始上下文不被污染。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from src.agent.protocols import AgentContext, AgentOpinion, StageResult

# 最大并发工作线程数，避免过多线程导致资源竞争
MAX_SKILL_WORKERS = 4


@dataclass
class SkillRunResult:
    """单个专家技能阶段的执行结果，包含阶段信息和产生的意见列表。"""

    stage: StageResult
    opinions: list[AgentOpinion]


def run_concurrently(agents: list[Any], ctx: AgentContext, runner: Callable[[Any, AgentContext], StageResult], *, max_workers: int = 3) -> list[SkillRunResult]:
    """并发运行多个技能 Agent，并按原始路由顺序返回结果。

    Args:
        agents: 要并发执行的专家技能 Agent 列表。
        ctx: 当前分析上下文。
        runner: 执行单个 Agent 的可调用对象。
        max_workers: 最大并发工作线程数（默认 3）。

    Returns:
        按原始顺序排列的 SkillRunResult 列表。
    """
    if not agents:
        return []

    # 计算实际工作线程数：不超过配置值、最大限制和 Agent 数量
    worker_count = max(1, min(int(max_workers or 1), len(agents), MAX_SKILL_WORKERS))
    results: dict[int, SkillRunResult] = {}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="agent-skill") as pool:
        # 为每个 Agent 提交任务，保留原始索引以便按顺序返回
        futures = {
            pool.submit(copy_context().run, _run_one, agent, ctx, runner): index
            for index, agent in enumerate(agents)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [results[index] for index in range(len(agents))]


def _run_one(agent: Any, source: AgentContext, runner: Callable[[Any, AgentContext], StageResult]) -> SkillRunResult:
    """执行单个技能 Agent，并收集其产生的意见。

    首先克隆上下文以实现隔离，然后运行 Agent，
    最后提取该 Agent 产生的新意见。
    """
    isolated = _clone_context(source)
    existing_opinion_count = len(isolated.opinions)
    stage = runner(agent, isolated)
    opinions = [stage.opinion] if stage.opinion is not None else isolated.opinions[existing_opinion_count:]
    return SkillRunResult(stage=stage, opinions=[opinion for opinion in opinions if opinion is not None])


def _clone_context(source: AgentContext) -> AgentContext:
    """克隆可变载荷，使专家工作人员无法修改共享状态。

    深度复制数据、风险标记和元数据等可变字段，
    保持股票代码、名称等不可变字段的引用效率。
    """
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
