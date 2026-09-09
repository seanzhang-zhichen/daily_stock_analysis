"""User feedback persistence for decision signals."""
from __future__ import annotations
from typing import Optional
from src.storage import DatabaseManager, DecisionSignalFeedbackRecord, DecisionSignalRecord
from sqlalchemy import select

class DecisionSignalFeedbackService:
    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or DatabaseManager.get_instance()

    def _signal(self, session, signal_id: int, user_id: Optional[int]):
        return session.execute(select(DecisionSignalRecord).where(
            DecisionSignalRecord.id == signal_id,
            DecisionSignalRecord.user_id == user_id,
        )).scalar_one_or_none()

    def get(self, signal_id: int, *, user_id: Optional[int]):
        with self.db.get_session() as session:
            if self._signal(session, signal_id, user_id) is None:
                return None
            row = session.execute(select(DecisionSignalFeedbackRecord).where(
                DecisionSignalFeedbackRecord.signal_id == signal_id,
                DecisionSignalFeedbackRecord.user_id == user_id,
            )).scalar_one_or_none()
            return self._serialize(signal_id, row)

    def put(self, signal_id: int, *, user_id: Optional[int], feedback_value: str,
            reason_code: Optional[str] = None, note: Optional[str] = None, source: str = "api"):
        if feedback_value not in {"useful", "not_useful"}:
            raise ValueError("feedback_value must be useful or not_useful")
        with self.db.get_session() as session:
            if self._signal(session, signal_id, user_id) is None:
                return None
            row = session.execute(select(DecisionSignalFeedbackRecord).where(
                DecisionSignalFeedbackRecord.signal_id == signal_id,
                DecisionSignalFeedbackRecord.user_id == user_id,
            )).scalar_one_or_none()
            if row is None:
                row = DecisionSignalFeedbackRecord(signal_id=signal_id, user_id=user_id)
                session.add(row)
            row.feedback_value, row.reason_code, row.note, row.source = feedback_value, reason_code, note, source
            session.commit(); session.refresh(row)
            return self._serialize(signal_id, row)

    @staticmethod
    def _serialize(signal_id, row):
        return {"signal_id": signal_id, "feedback_value": getattr(row, "feedback_value", None),
                "reason_code": getattr(row, "reason_code", None), "note": getattr(row, "note", None),
                "source": getattr(row, "source", None),
                "created_at": getattr(getattr(row, "created_at", None), "isoformat", lambda: None)(),
                "updated_at": getattr(getattr(row, "updated_at", None), "isoformat", lambda: None)()}
