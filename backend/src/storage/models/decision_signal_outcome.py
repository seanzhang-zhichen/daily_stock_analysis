# -*- coding: utf-8 -*-
"""A 股决策信号的前向日线回测结果模型。"""

from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Index, Integer, String, UniqueConstraint

from src.storage.base import Base


class DecisionSignalOutcomeRecord(Base):
    """一条 ``DecisionSignalRecord`` 在某评估窗口/引擎版本下的实际命中情况。

    用于评估 AI 决策信号的准确性，记录信号发出后的实际表现：
    - 价格走势（start_price / end_close / max_high / min_low）
    - 方向判断正确性（direction_expected / direction_correct）
    - 持仓状态（holding_state）
    - 无法评估原因（unable_reason）

    通过 ``(signal_id, horizon, engine_version)`` 唯一约束
    保证同一信号在同一评估窗口和引擎版本下只有一条结果记录。
    """

    __tablename__ = "decision_signal_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, nullable=False, index=True)
    # 评估时间周期：short / medium / long
    horizon = Column(String(16), nullable=False, index=True)
    engine_version = Column(String(32), nullable=False, index=True)
    # 评估状态：unable（无法评估）/ pending（待评估）/ completed（已完成）
    eval_status = Column(String(24), nullable=False, default="unable", index=True)
    # 结果：win / loss / neutral
    outcome = Column(String(16), index=True)
    # 预期方向：up / down / flat
    direction_expected = Column(String(16), index=True)
    direction_correct = Column(Boolean)
    # 无法评估原因：insufficient_data / market_closed / etc.
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
    # 持仓状态：unknown / holding / exited / stopped
    holding_state = Column(String(16), nullable=False, default="unknown", index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)

    __table_args__ = (
        # 唯一约束：同一信号在同一评估窗口和引擎版本下只有一条结果
        UniqueConstraint("signal_id", "horizon", "engine_version", name="uix_decision_signal_outcome_key"),
        # 复合索引：按引擎版本、操作、时间周期统计结果分布
        Index("ix_decision_signal_outcome_stats_action", "engine_version", "action", "horizon"),
    )


__all__ = ["DecisionSignalOutcomeRecord"]
