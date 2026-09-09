from contextvars import ContextVar
from time import sleep
from types import SimpleNamespace

from src.agent.protocols import AgentContext, AgentOpinion, StageResult, StageStatus
from src.agent.skills.scheduler import run_concurrently


def test_parallel_skill_contexts_are_isolated_and_results_keep_input_order():
    source = AgentContext(data={"nested": {"state": "original"}}, meta={"source": "main"})
    agents = [SimpleNamespace(agent_name="skill_slow", delay=0.03), SimpleNamespace(agent_name="skill_fast", delay=0)]

    def runner(agent, context):
        sleep(agent.delay)
        context.data["nested"]["state"] = agent.agent_name
        opinion = AgentOpinion(agent_name=agent.agent_name, signal="hold", confidence=0.5)
        context.add_opinion(opinion)
        return StageResult(stage_name=agent.agent_name, status=StageStatus.COMPLETED, opinion=opinion)

    results = run_concurrently(agents, source, runner, max_workers=4)

    assert [result.stage.stage_name for result in results] == ["skill_slow", "skill_fast"]
    assert source.data["nested"]["state"] == "original"
    assert source.opinions == []


def test_parallel_skill_workers_receive_callers_contextvars():
    request_id = ContextVar("request_id", default="missing")
    request_id.set("analysis-42")
    agents = [SimpleNamespace(agent_name="skill_one"), SimpleNamespace(agent_name="skill_two")]

    def runner(agent, context):
        opinion = AgentOpinion(agent_name=agent.agent_name, signal="hold", confidence=0.5, raw_data={"request_id": request_id.get()})
        return StageResult(stage_name=agent.agent_name, status=StageStatus.COMPLETED, opinion=opinion)

    results = run_concurrently(agents, AgentContext(), runner, max_workers=2)

    assert [result.opinions[0].raw_data["request_id"] for result in results] == ["analysis-42", "analysis-42"]
