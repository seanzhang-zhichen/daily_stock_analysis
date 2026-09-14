# -*- coding: utf-8 -*-
"""决策信号反馈（Decision Signal Feedback）数据模型。

记录用户对 AI 决策信号（如买入/卖出/观望建议）的反馈信息，
用于收集用户对信号质量的评价，支持信号模型的持续优化。

反馈类型：
- **有用（helpful）**：用户认为信号有帮助
- **无用（not_helpful）**：用户认为信号没有帮助
- **错误（wrong）**：用户认为信号方向或判断有误

设计约束：
- 每个用户对同一条信号只能反馈一次（通过 ``signal_id`` + ``user_id`` 唯一约束保证）。
- 反馈来源可以是 API、Web 界面或 Bot 等渠道。
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from src.storage.base import Base


class DecisionSignalFeedbackRecord(Base):
    """用户对决策信号的反馈记录表。

    用于收集用户对 AI 生成决策信号的评价，支持按信号和用户维度查询，
    为信号质量评估和模型优化提供数据支撑。

    属性:
        id: 自增主键，唯一标识一条反馈记录。
        signal_id: 关联的决策信号 ID（对应 ``decision_signals`` 表）。
        user_id: 反馈用户的 ID（To C 多用户隔离）；Bot / CLI 路径可为空。
        feedback_value: 反馈值，如 ``helpful``、``not_helpful``、``wrong`` 等。
        reason_code: 反馈原因代码，用于分类统计（如 ``too_bearish``、``too_bullish``）。
        note: 用户的详细反馈备注（可选）。
        source: 反馈来源渠道，如 ``api``、``web``、``bot`` 等。
        created_at: 记录创建时间。
        updated_at: 记录最后更新时间。
    """

    __tablename__ = "decision_signal_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 关联的决策信号 ID，建立与 decision_signals 表的关联
    signal_id = Column(Integer, nullable=False, index=True)
    # To C 多用户隔离：反馈归属的用户 ID；匿名反馈可为空
    user_id = Column(Integer, nullable=True, index=True)
    # 反馈值：helpful / not_helpful / wrong 等
    feedback_value = Column(String(24), nullable=False)
    # 反馈原因代码，用于分类统计和分析
    reason_code = Column(String(64))
    # 用户的详细反馈备注
    note = Column(Text)
    # 反馈来源渠道：api / web / bot 等
    source = Column(String(16), nullable=False, default="api")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    # 唯一约束：每个用户对同一条信号只能反馈一次
    __table_args__ = (
        UniqueConstraint(
            "signal_id",
            "user_id",
            name="uix_decision_signal_feedback_owner",
        ),
    )
