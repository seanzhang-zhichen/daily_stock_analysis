# -*- coding: utf-8 -*-
"""Regression coverage for the ported AI decision-signal module."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from api.v1.endpoints.decision_signals import (
    get_latest_decision_signals,
    list_decision_signals,
    update_decision_signal_status,
)
from api.v1.schemas.decision_signals import DecisionSignalStatusUpdateRequest
from src.services.decision_signal_service import (
    DecisionSignalNotFoundError,
    DecisionSignalService,
)
from src.storage import AnalysisHistory, DatabaseManager


@pytest.fixture()
def db():
    DatabaseManager.reset_instance()
    manager = DatabaseManager(db_url="sqlite:///:memory:")
    yield manager
    DatabaseManager.reset_instance()


def _result(*, code: str, action: str, score: int, risk: str = "") -> SimpleNamespace:
    decision_type = "buy" if action == "买入" else "sell" if action == "卖出" else "hold"
    return SimpleNamespace(
        code=code,
        name="测试股票",
        sentiment_score=score,
        operation_advice=action,
        trend_prediction="看多" if score >= 60 else "看空",
        analysis_summary=f"{action}摘要",
        decision_type=decision_type,
        confidence_level="高",
        dashboard={
            "core_conclusion": {
                "one_sentence": f"当前建议{action}",
                "time_sensitivity": "本周内",
                "position_advice": {
                    "no_position": "等待确认后执行",
                    "has_position": "遵守止损计划",
                },
            },
            "intelligence": {
                "risk_alerts": [risk] if risk else [],
                "positive_catalysts": ["行业景气改善"],
            },
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "100",
                    "secondary_buy": "98",
                    "stop_loss": "92",
                    "take_profit": "118",
                },
                "action_checklist": ["量能确认", "不追高"],
            },
        },
        data_sources="test",
        raw_response=None,
        to_dict=lambda: {
            "code": code,
            "name": "测试股票",
            "sentiment_score": score,
            "operation_advice": action,
            "trend_prediction": "看多" if score >= 60 else "看空",
            "analysis_summary": f"{action}摘要",
            "decision_type": decision_type,
            "confidence_level": "高",
            "report_language": "zh",
            "dashboard": {
                "core_conclusion": {
                    "one_sentence": f"当前建议{action}",
                    "time_sensitivity": "本周内",
                    "position_advice": {
                        "no_position": "等待确认后执行",
                        "has_position": "遵守止损计划",
                    },
                },
                "intelligence": {
                    "risk_alerts": [risk] if risk else [],
                    "positive_catalysts": ["行业景气改善"],
                },
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "100",
                        "secondary_buy": "98",
                        "stop_loss": "92",
                        "take_profit": "118",
                    },
                    "action_checklist": ["量能确认", "不追高"],
                },
            },
            "data_sources": "test",
        },
        get_sniper_points=lambda: {
            "ideal_buy": "100",
            "secondary_buy": "98",
            "stop_loss": "92",
            "take_profit": "118",
        },
    )


def _save(db: DatabaseManager, *, user_id: int, query_id: str, action: str, score: int) -> int:
    assert db.save_analysis_history(
        _result(code="600519", action=action, score=score, risk="估值偏高"),
        query_id=query_id,
        report_type="full",
        news_content="news",
        save_snapshot=False,
        user_id=user_id,
    ) == 1
    with db.get_session() as session:
        return session.query(AnalysisHistory.id).filter(AnalysisHistory.query_id == query_id).scalar()


def test_sync_extracts_structured_signal_and_is_idempotent(db: DatabaseManager) -> None:
    report_id = _save(db, user_id=7, query_id="signal-buy", action="买入", score=82)
    service = DecisionSignalService(db)

    assert service.sync_analysis_history(user_id=7) == 1
    assert service.sync_analysis_history(user_id=7) == 0

    result = service.list_signals(user_id=7, status=None)
    assert result["total"] == 1
    item = result["items"][0]
    assert item["source_report_id"] == report_id
    assert item["action"] == "buy"
    assert item["horizon"] == "5d"
    assert item["entry_low"] == 98
    assert item["entry_high"] == 100
    assert item["stop_loss"] == 92
    assert item["target_price"] == 118
    assert item["risk_summary"] == "估值偏高"
    assert item["metadata"]["position_advice"]["no_position"] == "等待确认后执行"


def test_new_opposing_signal_invalidates_older_signal(db: DatabaseManager) -> None:
    _save(db, user_id=7, query_id="signal-buy", action="买入", score=80)
    _save(db, user_id=7, query_id="signal-sell", action="卖出", score=18)

    items = DecisionSignalService(db).list_signals(user_id=7, status=None)["items"]

    assert [item["action"] for item in items] == ["sell", "buy"]
    assert items[0]["status"] == "active"
    assert items[1]["status"] == "invalidated"


def test_queries_and_status_updates_are_user_isolated(db: DatabaseManager) -> None:
    _save(db, user_id=7, query_id="owner-seven", action="持有", score=55)
    _save(db, user_id=8, query_id="owner-eight", action="买入", score=75)
    service = DecisionSignalService(db)

    owner_items = service.list_signals(user_id=7, status=None)["items"]
    other_items = service.list_signals(user_id=8, status=None)["items"]
    assert len(owner_items) == len(other_items) == 1
    assert owner_items[0]["action"] == "hold"
    assert other_items[0]["action"] == "buy"

    updated = service.update_status(owner_items[0]["id"], user_id=7, status="archived")
    assert updated["status"] == "archived"
    with pytest.raises(DecisionSignalNotFoundError):
        service.get_signal(owner_items[0]["id"], user_id=8)


def test_market_review_history_is_not_backfilled(db: DatabaseManager) -> None:
    with db.get_session() as session:
        session.add(AnalysisHistory(
            user_id=7,
            query_id="market-review",
            code="MARKET",
            name="大盘复盘",
            report_type="market_review",
            sentiment_score=50,
            operation_advice="查看复盘",
            trend_prediction="震荡",
            analysis_summary="复盘",
            raw_result="{}",
            created_at=datetime.now(),
        ))
        session.commit()

    assert DecisionSignalService(db).sync_analysis_history(user_id=7) == 0


def test_decision_signal_api_lists_latest_and_archives(db: DatabaseManager) -> None:
    _save(db, user_id=7, query_id="api-signal", action="买入", score=82)
    current_user = SimpleNamespace(id=7)

    listed = list_decision_signals(
        market=None,
        stock_code=None,
        action=None,
        status="active",
        created_from=None,
        created_to=None,
        page=1,
        page_size=20,
        current_user=current_user,
        db_manager=db,
    )
    assert listed.total == 1
    assert listed.items[0].stock_code == "600519"
    signal_id = listed.items[0].id

    latest = get_latest_decision_signals(
        "600519",
        current_user=current_user,
        db_manager=db,
    )
    assert latest.items[0].id == signal_id

    archived = update_decision_signal_status(
        signal_id,
        DecisionSignalStatusUpdateRequest(status="archived"),
        current_user=current_user,
        db_manager=db,
    )
    assert archived.item.status == "archived"

    latest_after_archive = get_latest_decision_signals(
        "600519",
        current_user=current_user,
        db_manager=db,
    )
    assert latest_after_archive.items == []
