from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace

from src.analyzer import AnalysisResult
from src.daily_market_context_guardrail import apply_daily_market_context_guardrail
from src.services.daily_market_context import (
    DailyMarketContextService,
    format_daily_market_context_prompt_section,
)


def test_conservative_a_share_context_softens_aggressive_buy() -> None:
    result = AnalysisResult(
        code="600519",
        name="Kweichow Moutai",
        sentiment_score=88,
        trend_prediction="Bullish",
        operation_advice="Buy now and add aggressively",
        decision_type="buy",
        confidence_level="High",
        dashboard={"decision_type": "buy", "operation_advice": "Buy now and add aggressively"},
    )

    adjustments = apply_daily_market_context_guardrail(
        result,
        daily_market_context={
            "region": "cn",
            "trade_date": "2026-09-04",
            "summary": "Market cooling and elevated risk",
            "risk_tags": ["high_risk", "market_cooling"],
        },
        report_language="en",
    )

    assert "daily_market_context_buy_softened" in adjustments
    assert result.decision_type == "hold"
    assert result.operation_advice == "Watch"
    assert result.sentiment_score == 52


def test_daily_context_service_reads_same_day_a_share_history() -> None:
    today = date(2026, 9, 4)
    record = SimpleNamespace(
        report_type="market_review",
        context_snapshot=json.dumps({"market_review_region": "cn", "report_language": "en"}),
        raw_result=json.dumps({"raw_response": "Market cooling and elevated risk"}),
        news_content="Market cooling and elevated risk",
        analysis_summary="Market cooling and elevated risk",
        created_at=datetime(2026, 9, 4, 12, 0),
        id=7,
        query_id="market-review-1",
    )
    db = SimpleNamespace(get_analysis_history=lambda **kwargs: [record])
    service = DailyMarketContextService(db_manager=db, today_fn=lambda: today)

    context = service.get_context(
        region="cn",
        config=SimpleNamespace(report_language="en"),
        notifier=None,
        allow_generate=False,
        target_date=today,
    )

    assert context is not None
    assert context.region == "cn"
    assert context.source == "analysis_history"
    assert "cooling" in context.summary.lower()


def test_prompt_section_marks_summary_as_untrusted_background() -> None:
    section = format_daily_market_context_prompt_section(
        {
            "region": "cn",
            "trade_date": "2026-09-04",
            "summary": "Market cooling",
            "risk_tags": ["market_cooling"],
        },
        report_language="en",
    )

    assert "Daily Market Context" in section
    assert "BEGIN_UNTRUSTED_MARKET_SUMMARY" in section
    assert "avoid aggressive buy advice" in section
