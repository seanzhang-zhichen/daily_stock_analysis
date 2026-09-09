from src.analyzer import AnalysisResult
from src.schemas.decision_scale import apply_score_action_scale


def _result(score, decision_type="hold", advice="观望"):
    return AnalysisResult(code="600519", name="test", sentiment_score=score, trend_prediction="震荡", operation_advice=advice, decision_type=decision_type)


def test_score_bands_produce_canonical_actions_and_audit_records():
    cases = [(80, "buy"), (60, "buy"), (40, "watch"), (20, "reduce"), (0, "sell")]
    for score, action in cases:
        result = _result(score)
        records = apply_score_action_scale(result)
        assert result.decision_action == action
        assert records[0]["score_band"]
        assert result.dashboard["decision_guardrails"][-1]["after_action"] == action


def test_score_alignment_normalizes_inconsistent_action_without_erasing_audit():
    result = _result(15, decision_type="buy", advice="买入")
    apply_score_action_scale(result)
    assert result.decision_type == "sell"
    assert result.decision_action == "sell"
    assert result.decision_guardrails[-1]["before_action"] == "buy"
