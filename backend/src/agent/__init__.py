# -*- coding: utf-8 -*-
"""
股票分析系统的 Agent 模块。

提供基于 LLM、具备工具调用能力的 Agent，支持可插拔交易策略与多轮对话。
通过环境变量 AGENT_MODE=true 启用。

建议使用显式导入，避免在仅需 tools.registry 等轻量子模块时
连带引入 json_repair 等重型依赖::

    from src.agent.executor import AgentExecutor, AgentResult
    from src.agent.runner import run_agent_loop, RunLoopResult
    from src.agent.protocols import AgentContext, AgentOpinion, StageResult, AgentRunStats
    from src.agent.orchestrator import AgentOrchestrator
"""


def __getattr__(name):
    """惰性导入，避免访问包时触发 json_repair 等重依赖。"""
    if name == "AgentExecutor":
        from src.agent.executor import AgentExecutor
        return AgentExecutor
    if name == "AgentResult":
        from src.agent.executor import AgentResult
        return AgentResult
    if name == "RunLoopResult":
        from src.agent.runner import RunLoopResult
        return RunLoopResult
    if name in ("AgentContext", "AgentOpinion", "StageResult", "AgentRunStats"):
        from src.agent import protocols
        return getattr(protocols, name)
    if name == "AgentOrchestrator":
        from src.agent.orchestrator import AgentOrchestrator
        return AgentOrchestrator
    if name == "AgentMemory":
        from src.agent.memory import AgentMemory
        return AgentMemory
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentExecutor",
    "AgentResult",
    "RunLoopResult",
    "AgentContext",
    "AgentOpinion",
    "StageResult",
    "AgentRunStats",
    "AgentOrchestrator",
    "AgentMemory",
]
