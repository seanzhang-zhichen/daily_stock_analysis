from datetime import datetime

from src.core.trading_calendar import build_market_phase_context
from src.market_phase_summary import (
    MARKET_PHASE_SUMMARY_KEY,
    format_public_market_status_line,
    render_market_phase_summary,
)


def test_explicit_a_share_intraday_phase_is_rendered_as_public_summary() -> None:
    context = build_market_phase_context(
        market="cn",
        current_time=datetime(2026, 9, 8, 10, 30),
        trigger_source="api",
        analysis_phase="intraday",
    )

    summary = render_market_phase_summary(context.to_dict())

    assert summary is not None
    assert summary["phase"] == "intraday"
    assert summary["market"] == "cn"
    assert summary["market_local_time"] == "2026-09-08T10:30:00+08:00"
    assert summary["session_date"] == "2026-09-08"
    assert summary["effective_daily_bar_date"] == "2026-09-07"
    assert summary["is_trading_day"] is True
    assert summary["is_market_open_now"] is True
    assert summary["is_partial_bar"] is True
    assert summary["trigger_source"] == "api"
    assert summary["analysis_intent"] == "intraday"
    assert MARKET_PHASE_SUMMARY_KEY == "market_phase_summary"
    assert format_public_market_status_line(summary) == "市场状态：A股 · 盘中"
