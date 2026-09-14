from src.services.research_artifact_service import build_research_artifact


def test_research_artifact_is_additive_and_stable():
    artifact = build_research_artifact({
        "stock_code": "600519", "stock_name": "贵州茅台", "created_at": "2026-09-11",
        "analysis_summary": "基本面稳健", "operation_advice": "持有", "trend_prediction": "震荡向上",
        "raw_result": {"decision_signal": {"risks": ["估值偏高"], "invalidators": ["业绩失速"]}},
    })
    assert artifact["version"] == "1.0"
    assert artifact["thesis"] == ["基本面稳健", "持有"]
    assert artifact["risks"] == ["估值偏高"]
    assert artifact["invalidation_conditions"] == ["业绩失速"]
