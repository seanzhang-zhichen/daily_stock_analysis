"""决策信号的用户反馈持久化服务。

负责存储和查询用户对决策信号（decision signal）的反馈，
支持"有用"和"无用"两种反馈类型，并记录反馈原因和备注。
"""
from __future__ import annotations
from typing import Optional
from src.storage import DatabaseManager, DecisionSignalFeedbackRecord, DecisionSignalRecord
from sqlalchemy import select


class DecisionSignalFeedbackService:
    """决策信号反馈服务：管理用户对决策信号的反馈数据。"""

    def __init__(self, db: Optional[DatabaseManager] = None):
        """初始化反馈服务。

        Args:
            db: 数据库管理器实例，默认使用全局单例。
        """
        self.db = db or DatabaseManager.get_instance()

    def _signal(self, session, signal_id: int, user_id: Optional[int]):
        """查询指定用户归属的决策信号是否存在。

        Args:
            session: 数据库会话。
            signal_id: 决策信号 ID。
            user_id: 用户 ID。

        Returns:
            信号记录对象，若不存在则返回 None。
        """
        return session.execute(select(DecisionSignalRecord).where(
            DecisionSignalRecord.id == signal_id,
            DecisionSignalRecord.user_id == user_id,
        )).scalar_one_or_none()

    def get(self, signal_id: int, *, user_id: Optional[int]):
        """查询指定决策信号的用户反馈。

        Args:
            signal_id: 决策信号 ID。
            user_id: 用户 ID。

        Returns:
            反馈数据的序列化字典，若信号不存在则返回 None。
        """
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
        """保存或更新用户对决策信号的反馈。

        Args:
            signal_id: 决策信号 ID。
            user_id: 用户 ID。
            feedback_value: 反馈值，仅允许 "useful" 或 "not_useful"。
            reason_code: 反馈原因代码，可选。
            note: 用户备注，可选。
            source: 反馈来源标识，默认为 "api"。

        Returns:
            更新后的反馈数据字典，若信号不存在则返回 None。

        Raises:
            ValueError: 若 feedback_value 不在允许范围内。
        """
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
            session.commit()
            session.refresh(row)
            return self._serialize(signal_id, row)

    @staticmethod
    def _serialize(signal_id, row):
        """将反馈记录序列化为字典格式。

        Args:
            signal_id: 决策信号 ID。
            row: 数据库记录对象。

        Returns:
            包含反馈字段的字典。
        """
        return {"signal_id": signal_id, "feedback_value": getattr(row, "feedback_value", None),
                "reason_code": getattr(row, "reason_code", None), "note": getattr(row, "note", None),
                "source": getattr(row, "source", None),
                "created_at": getattr(getattr(row, "created_at", None), "isoformat", lambda: None)(),
                "updated_at": getattr(getattr(row, "updated_at", None), "isoformat", lambda: None)()}
