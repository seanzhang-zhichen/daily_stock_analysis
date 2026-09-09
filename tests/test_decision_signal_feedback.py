from datetime import datetime

from src.services.decision_signal_feedback_service import DecisionSignalFeedbackService
from src.storage import DatabaseManager, DecisionSignalRecord


def test_feedback_is_upserted_and_user_isolated():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    with db.get_session() as session:
        row = DecisionSignalRecord(
            user_id=7, stock_code="600519", market="cn", source_type="analysis",
            source_report_id=1, trigger_source="test", action="hold",
            plan_quality="minimal", status="active", created_at=datetime.now(), updated_at=datetime.now(),
        )
        session.add(row); session.commit(); signal_id = row.id
    service = DecisionSignalFeedbackService(db)
    assert service.get(signal_id, user_id=8) is None
    saved = service.put(signal_id, user_id=7, feedback_value="useful", note="ok")
    assert saved["feedback_value"] == "useful"
    updated = service.put(signal_id, user_id=7, feedback_value="not_useful", reason_code="wrong")
    assert updated["feedback_value"] == "not_useful"
    assert service.get(signal_id, user_id=7)["reason_code"] == "wrong"
    DatabaseManager.reset_instance()
