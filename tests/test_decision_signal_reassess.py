import json
import pytest
from datetime import datetime
from src.services.decision_signal_reassess_service import DecisionSignalReassessService
from src.storage import AnalysisHistory, DatabaseManager

def test_reassess_preview_and_persist_are_user_scoped():
    DatabaseManager.reset_instance(); db = DatabaseManager(db_url="sqlite:///:memory:")
    with db.get_session() as session:
        row = AnalysisHistory(user_id=7, query_id="r1", code="600519", name="Test", report_type="full",
            sentiment_score=82, operation_advice="买入", analysis_summary="summary",
            raw_result=json.dumps({"sentiment_score":82,"operation_advice":"买入","confidence_level":"High"}), created_at=datetime.now())
        session.add(row); session.commit(); rid=row.id
    service=DecisionSignalReassessService(db)
    with pytest.raises(LookupError):
        service.reassess(source_report_id=rid, user_id=8)
    preview=service.reassess(source_report_id=rid, user_id=7)
    assert preview["preview"]["action"] == "watch"
    saved=service.reassess(source_report_id=rid, user_id=7, persist=True)
    assert saved["item"]["action"] == "watch"
    DatabaseManager.reset_instance()
