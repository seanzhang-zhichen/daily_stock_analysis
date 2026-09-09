"""User feedback attached to a decision signal."""
from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
from src.storage.base import Base


class DecisionSignalFeedbackRecord(Base):
    __tablename__ = "decision_signal_feedback"
    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    feedback_value = Column(String(24), nullable=False)
    reason_code = Column(String(64))
    note = Column(Text)
    source = Column(String(16), nullable=False, default="api")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    __table_args__ = (UniqueConstraint("signal_id", "user_id", name="uix_decision_signal_feedback_owner"),)

