# -*- coding: utf-8 -*-
from datetime import date, datetime

import pytest

from src.services.decision_signal_outcome_service import DecisionSignalOutcomeService
from src.storage import DatabaseManager, DecisionSignalRecord, StockDaily


@pytest.fixture()
def db():
    DatabaseManager.reset_instance()
    manager = DatabaseManager(db_url="sqlite:///:memory:")
    yield manager
    DatabaseManager.reset_instance()


def _signal(db, *, action="buy", market="cn", source_report_id=1):
    with db.get_session() as session:
        row = DecisionSignalRecord(
            user_id=7, stock_code="600519", stock_name="Test", market=market,
            source_type="analysis", source_report_id=source_report_id, trigger_source="test",
            action=action, plan_quality="complete", status="active",
            created_at=datetime(2024, 1, 2, 10, 0, 0), updated_at=datetime.now(),
        )
        session.add(row)
        session.commit()
        return row.id


def _bars(db, closes):
    with db.get_session() as session:
        for offset, close in enumerate(closes):
            session.add(StockDaily(code="600519", date=date(2024, 1, 2 + offset), close=close, high=close + 1, low=close - 1))
        session.commit()


def test_a_share_outcome_evaluates_direction_and_is_idempotent(db):
    signal_id = _signal(db)
    _bars(db, [100, 102, 103, 105])
    service = DecisionSignalOutcomeService(db)

    first = service.run(signal_id=signal_id, user_id=7, horizons=["3d"])
    second = service.run(signal_id=signal_id, user_id=7, horizons=["3d"])

    item = first["items"][0]
    assert item["eval_status"] == "completed"
    assert item["outcome"] == "hit"
    assert item["stock_return_pct"] == 5.0
    assert second["evaluated"] == 0
    assert second["skipped"] == 1


def test_outcome_retries_insufficient_daily_bars_and_rejects_non_a_share(db):
    signal_id = _signal(db)
    _bars(db, [100, 102])
    service = DecisionSignalOutcomeService(db)

    incomplete = service.run(signal_id=signal_id, user_id=7, horizons=["3d"])
    with db.get_session() as session:
        session.add(StockDaily(code="600519", date=date(2024, 1, 4), close=103, high=104, low=102))
        session.add(StockDaily(code="600519", date=date(2024, 1, 5), close=104, high=105, low=103))
        session.commit()
    completed = service.run(signal_id=signal_id, user_id=7, horizons=["3d"])

    assert incomplete["items"][0]["unable_reason"] == "insufficient_forward_bars"
    assert completed["items"][0]["eval_status"] == "completed"
    with pytest.raises(ValueError, match="Only A-share"):
        service.run(signal_id=_signal(db, market="hk", source_report_id=2), user_id=7)


def test_outcome_stats_are_scoped_to_signal_owner(db):
    signal_id = _signal(db)
    _bars(db, [100, 102, 103, 105])
    service = DecisionSignalOutcomeService(db)
    service.run(signal_id=signal_id, user_id=7, horizons=["3d"])
    stats = service.stats(user_id=7, horizon="3d")
    assert stats["total"] == 1
    assert stats["hit"] == 1
    assert stats["hit_rate_pct"] == 100.0
    assert service.stats(user_id=8)["total"] == 0
