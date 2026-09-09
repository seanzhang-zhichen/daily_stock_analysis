"""Regression coverage for persisted analysis-run diagnostics."""

from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    build_run_diagnostic_summary,
    current_diagnostic_snapshot,
    record_llm_run,
    reset_run_diagnostic_context,
)
from src.services.run_flow import build_history_run_flow_snapshot


def test_summary_exposes_trace_and_redacts_llm_error():
    token = activate_run_diagnostic_context(
        trace_id="trace-600519",
        query_id="query-600519",
        stock_code="600519",
        trigger_source="api",
    )
    try:
        record_llm_run(
            success=False,
            model="test-model",
            call_type="analysis",
            error_type="TimeoutError",
            error_message="Bearer secret-token failed at https://example.com/webhook?token=secret",
        )
        summary = build_run_diagnostic_summary(
            context_snapshot={"diagnostics": current_diagnostic_snapshot()},
            report_saved=True,
        )
    finally:
        reset_run_diagnostic_context(token)

    assert summary["trace_id"] == "trace-600519"
    assert summary["query_id"] == "query-600519"
    assert summary["stock_code"] == "600519"
    assert summary["status"] == "failed"
    assert "secret-token" not in summary["copy_text"]
    assert "example.com/webhook" not in summary["copy_text"]


def test_history_flow_uses_sanitized_persisted_diagnostics():
    class Record:
        id = 17
        query_id = "query-600519"
        code = "600519"
        name = "贵州茅台"
        report_type = "detailed"
        context_snapshot = {
            "diagnostics": {
                "trace_id": "trace-600519",
                "query_id": "query-600519",
                "provider_runs": [{"data_type": "daily_data", "provider": "akshare", "success": True, "latency_ms": 12}],
                "llm_runs": [{"model": "model-a", "success": False, "error_message": "Bearer secret-token"}],
            }
        }
        raw_result = {"code": "600519"}

    flow = build_history_run_flow_snapshot(Record())
    assert flow["trace_id"] == "trace-600519"
    assert flow["status"] == "failed"
    assert any(node["id"] == "provider_1" for node in flow["nodes"])
    assert "secret-token" not in str(flow)
