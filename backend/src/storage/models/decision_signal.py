# -*- coding: utf-8 -*-
"""从股票分析报告中抽取出的 AI 决策信号持久化模型。"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, UniqueConstraint

from src.storage.base import Base


class DecisionSignalRecord(Base):
    """从一份分析报告中抽取的结构化、用户维度的推荐记录。

    将 AI 分析结果转化为结构化的交易信号，包括：
    - 股票代码、市场、操作方向（buy/sell/hold）
    - 入场区间、止损价、目标价等交易参数
    - 置信度、评分、时间周期等元数据
    - 失效条件、观察条件、风险摘要等辅助信息

    通过 ``(user_id, source_type, source_report_id)`` 唯一约束
    保证同一用户同一来源的报告只产生一条信号记录。
    """

    __tablename__ = "decision_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)

    stock_code = Column(String(32), nullable=False, index=True)
    stock_name = Column(String(64))
    market = Column(String(16), nullable=False, index=True)

    source_type = Column(String(24), nullable=False, default="analysis", index=True)
    source_report_id = Column(Integer, nullable=False, index=True)
    trace_id = Column(String(64), index=True)
    trigger_source = Column(String(64), nullable=False, default="analysis_history")

    # 操作方向：buy / sell / hold / watch
    action = Column(String(16), nullable=False, index=True)
    action_label = Column(String(32))
    confidence = Column(Float)
    score = Column(Integer)
    # 时间周期：short / medium / long
    horizon = Column(String(16), index=True)

    # 交易参数
    entry_low = Column(Float)
    entry_high = Column(Float)
    stop_loss = Column(Float)
    target_price = Column(Float)
    invalidation = Column(Text)
    watch_conditions = Column(Text)
    reason = Column(Text)
    risk_summary = Column(Text)
    catalyst_summary = Column(Text)

    # 证据和元数据（JSON 字符串）
    evidence_json = Column(Text)
    data_quality_json = Column(Text)
    metadata_json = Column(Text)
    plan_quality = Column(String(16), nullable=False, default="unknown")

    # 状态管理
    status = Column(String(16), nullable=False, default="active", index=True)
    expires_at = Column(DateTime, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        # 唯一约束：同一用户同一来源的报告只产生一条信号记录
        UniqueConstraint(
            "user_id",
            "source_type",
            "source_report_id",
            name="uix_decision_signal_owner_source",
        ),
        # 复合索引：按用户、股票、状态和时间查询信号列表
        Index(
            "ix_decision_signal_owner_stock_status_time",
            "user_id",
            "stock_code",
            "status",
            "created_at",
        ),
        # 复合索引：按用户、市场、操作和时间查询信号列表
        Index(
            "ix_decision_signal_owner_market_action_time",
            "user_id",
            "market",
            "action",
            "created_at",
        ),
    )


__all__ = ["DecisionSignalRecord"]
