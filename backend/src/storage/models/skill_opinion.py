"""Immutable specialist opinions and their forward evaluation outcomes."""
from datetime import datetime
from sqlalchemy import Boolean, Column, Date, DateTime, Float, Index, Integer, String, UniqueConstraint
from src.storage.base import Base

class SkillOpinionSampleRecord(Base):
    __tablename__ = "skill_opinion_samples"
    id = Column(Integer, primary_key=True)
    analysis_history_id = Column(Integer, nullable=False, index=True)
    stock_code = Column(String(16), nullable=False, index=True)
    skill_id = Column(String(128), nullable=False, index=True)
    signal = Column(String(16), nullable=False)
    confidence = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    __table_args__ = (UniqueConstraint("analysis_history_id", "skill_id", name="uix_skill_opinion_sample"),)

class SkillOpinionOutcomeRecord(Base):
    __tablename__ = "skill_opinion_outcomes"
    id = Column(Integer, primary_key=True)
    sample_id = Column(Integer, nullable=False, index=True)
    horizon = Column(String(16), nullable=False)
    engine_version = Column(String(32), nullable=False)
    eval_status = Column(String(24), nullable=False, default="pending", index=True)
    outcome = Column(String(16), index=True)
    direction_correct = Column(Boolean)
    analysis_date = Column(Date, index=True)
    stock_return_pct = Column(Float)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    __table_args__ = (UniqueConstraint("sample_id", "horizon", "engine_version", name="uix_skill_opinion_outcome"), Index("ix_skill_opinion_outcome_performance", "engine_version", "eval_status", "horizon"))
