from src.agent.protocols import AgentOpinion
from src.agent.skills.synthesis import synthesize


def _opinion(skill_id: str, signal: str, confidence: float) -> AgentOpinion:
    return AgentOpinion(agent_name=f"skill_{skill_id}", signal=signal, confidence=confidence)


def test_high_confidence_directional_split_is_deescalated_to_watch():
    result = synthesize([
        _opinion("trend", "strong_buy", 0.9),
        _opinion("risk", "strong_sell", 0.8),
    ])

    assert result["conflicts"][0]["type"] == "directional_opposition"
    assert result["conflicts"][0]["severity"] == "high"
    assert result["final_signal"] == "hold"
    assert result["final_action"] == "watch"
    assert result["confidence"] < 0.9
    for response in result["deliberation"]["responses"]:
        assert response["revised_confidence"] <= next(
            opinion.confidence for opinion in [_opinion("trend", "strong_buy", 0.9), _opinion("risk", "strong_sell", 0.8)]
            if opinion.agent_name == f"skill_{response['skill_id']}"
        )


def test_aligned_skill_views_keep_coherent_trade_action():
    result = synthesize([
        _opinion("trend", "buy", 0.8),
        _opinion("breakout", "strong_buy", 0.7),
    ])

    assert result["conflicts"] == []
    assert result["final_signal"] in {"buy", "strong_buy"}
    assert result["final_action"] == "buy"
    assert result["deliberation"]["status"] == "skipped"
