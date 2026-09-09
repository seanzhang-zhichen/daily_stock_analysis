# -*- coding: utf-8 -*-
"""A 股决策信号的前向日线回测结果模型。"""

from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Index, Integer, String, UniqueConstraint

from src.storage.base import Base


class DecisionSignalOutcomeRecord(Base):
    """一条 ``DecisionSignalRecord`` 在某评估窗口/引擎版本下的实际命中情况。"""

    __tablename__ = "decision_signal_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, nullable=False, index=True)
    horizon = Column(String(16), nullable=False, index=True)
    engine_version = Column(String(32), nullable=False, index=True)
    eval_status = Column(String(24), nullable=False, default="unable", index=True)
    outcome = Column(String(16), index=True)
    direction_expected = Column(String(16), index=True)
    direction_correct = Column(Boolean)
    unable_reason = Column(String(64), index=True)
    anchor_date = Column(Date, index=True)
    eval_window_days = Column(Integer)
    start_price = Column(Float)
    end_close = Column(Float)
    max_high = Column(Float)
    min_low = Column(Float)
    stock_return_pct = Column(Float)
    action = Column(String(16), index=True)
    market = Column(String(8), index=True)
    holding_state = Column(String(16), nullable=False, default="unknown", index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        UniqueConstraint("signal_id", "horizon", "engine_version", name="uix_decision_signal_outcome_key"),
        Index("ix_decision_signal_outcome_stats_action", "engine_version", "action", "horizon"),
    )


__all__ = ["DecisionSignalOutcomeRecord"]
