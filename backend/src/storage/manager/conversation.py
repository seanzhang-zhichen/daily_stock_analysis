# -*- coding: utf-8 -*-
"""Agent 对话历史相关存取操作。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import and_, delete, desc, func, or_, select

from src.storage.models.conversation import ConversationMessage, ConversationSessionState, ConversationSummary
import json


class ConversationMixin:
    """Agent 对话历史 Mixin。"""

    def save_conversation_message(
        self,
        session_id: str,
        role: str,
        content: str,
        user_id: Optional[int] = None,
    ) -> None:
        """
        保存 Agent 对话消息。

        ``user_id`` 由 endpoint / 调用方按当前 ``AppUser`` 注入；Bot / CLI 路径可为空。
        """
        with self.session_scope() as session:
            msg = ConversationMessage(
                user_id=user_id,
                session_id=session_id,
                role=role,
                content=content
            )
            session.add(msg)

    def save_conversation_session_selected_skill_ids(self, session_id: str, skill_ids: List[str]) -> None:
        """持久化指定聊天会话显式选中的 skill 列表。"""
        with self.session_scope() as session:
            row = session.get(ConversationSessionState, session_id)
            payload = json.dumps(list(skill_ids), ensure_ascii=False)
            if row is None:
                session.add(ConversationSessionState(session_id=session_id, selected_skill_ids_json=payload))
            else:
                row.selected_skill_ids_json = payload

    def get_conversation_session_selected_skill_ids(self, session_id: str) -> Optional[List[str]]:
        """返回会话中持久化的 skill 列表；会话不存在或 JSON 损坏返回 ``None``。"""
        with self.session_scope() as session:
            row = session.get(ConversationSessionState, session_id)
            if row is None:
                return None
            try:
                value = json.loads(row.selected_skill_ids_json)
            except (TypeError, ValueError):
                return None
            return value if isinstance(value, list) else None

    def get_conversation_history(self, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """获取指定会话的 Agent 对话历史（按时间正序）。"""
        with self.session_scope() as session:
            stmt = select(ConversationMessage).filter(
                ConversationMessage.session_id == session_id
            ).order_by(ConversationMessage.created_at.desc()).limit(limit)
            messages = session.execute(stmt).scalars().all()

            # 倒序查询 + 末尾反转, 保证返回按时间正序排列
            return [{"role": msg.role, "content": msg.content} for msg in reversed(messages)]

    def get_visible_conversation_messages(
        self, session_id: str, limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """按时间正序返回 user / assistant 角色的对话消息。"""
        with self.session_scope() as session:
            stmt = select(ConversationMessage).where(
                ConversationMessage.session_id == session_id,
                ConversationMessage.role.in_(("user", "assistant")),
            )
            if limit is None:
                stmt = stmt.order_by(ConversationMessage.id)
            else:
                stmt = stmt.order_by(ConversationMessage.id.desc()).limit(limit)
            messages = session.execute(stmt).scalars().all()
            if limit is not None:
                messages.reverse()
            return [
                {"id": msg.id, "role": msg.role, "content": msg.content,
                 "created_at": msg.created_at}
                for msg in messages if msg.content
            ]

    def get_conversation_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """返回会话的滚动上下文摘要；不存在则返回 ``None``。"""
        with self.session_scope() as session:
            row = session.execute(
                select(ConversationSummary).where(ConversationSummary.session_id == session_id)
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "summary": row.summary,
                "covered_message_id": row.covered_message_id,
                "source_message_count": row.source_message_count,
                "estimated_tokens": row.estimated_tokens,
            }

    def upsert_conversation_summary(
        self, *, session_id: str, summary: str, covered_message_id: int,
        source_message_count: int, estimated_tokens: int,
    ) -> None:
        """新建或覆盖指定会话的滚动上下文摘要。"""
        with self.session_scope() as session:
            row = session.execute(
                select(ConversationSummary).where(ConversationSummary.session_id == session_id)
            ).scalar_one_or_none()
            if row is None:
                session.add(ConversationSummary(
                    session_id=session_id, summary=summary,
                    covered_message_id=covered_message_id,
                    source_message_count=source_message_count,
                    estimated_tokens=estimated_tokens,
                ))
                return
            row.summary = summary
            row.covered_message_id = covered_message_id
            row.source_message_count = source_message_count
            row.estimated_tokens = estimated_tokens

    def conversation_session_exists(self, session_id: str) -> bool:
        """当指定 ``session_id`` 下存在至少一条消息时返回 ``True``。"""
        with self.session_scope() as session:
            stmt = (
                select(ConversationMessage.id)
                .where(ConversationMessage.session_id == session_id)
                .limit(1)
            )
            return session.execute(stmt).scalar() is not None

    def get_chat_sessions(
        self,
        limit: int = 50,
        session_prefix: Optional[str] = None,
        extra_session_ids: Optional[List[str]] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取聊天会话列表（从 ``conversation_messages`` 聚合）。

        Args:
            limit: 返回会话的最大数量。
            session_prefix: 若提供，仅返回 ``session_id`` 以此前缀开头的会话；
                常用于按用户隔离（如 ``"telegram_12345"``）。
            extra_session_ids: 在前缀过滤之外，额外精确包含的 ``session_id`` 列表。
            user_id: To C 模式下按当前用户过滤；关闭时传 ``None``。

        Returns:
            按最近活跃时间倒序的会话列表，每条包含 ``session_id`` / ``title`` /
            ``message_count`` / ``last_active``。
        """
        with self.session_scope() as session:
            normalized_prefix = None
            if session_prefix:
                # 末尾强制带 ":", 避免前缀串误匹配其他用户同名 ID
                normalized_prefix = session_prefix if session_prefix.endswith(":") else f"{session_prefix}:"
            exact_ids = [sid for sid in (extra_session_ids or []) if sid]

            # 聚合每个 session 的消息数和最后活跃时间
            base = (
                select(
                    ConversationMessage.session_id,
                    func.count(ConversationMessage.id).label("message_count"),
                    func.min(ConversationMessage.created_at).label("created_at"),
                    func.max(ConversationMessage.created_at).label("last_active"),
                )
            )
            conditions = []
            if user_id is not None:
                conditions.append(ConversationMessage.user_id == user_id)
            elif normalized_prefix or exact_ids:
                # 无 user_id 过滤时按前缀/精确 ID 限定作用域, 防止跨用户越权
                prefix_conds = []
                if normalized_prefix:
                    prefix_conds.append(ConversationMessage.session_id.startswith(normalized_prefix))
                if exact_ids:
                    prefix_conds.append(ConversationMessage.session_id.in_(exact_ids))
                conditions.append(or_(*prefix_conds))
            if conditions:
                base = base.where(and_(*conditions))
            stmt = (
                base
                .group_by(ConversationMessage.session_id)
                .order_by(desc(func.max(ConversationMessage.created_at)))
                .limit(limit)
            )
            rows = session.execute(stmt).all()

            results = []
            for row in rows:
                sid = row.session_id
                # 取该会话第一条 user 消息作为标题
                first_user_msg = session.execute(
                    select(ConversationMessage.content)
                    .where(
                        and_(
                            ConversationMessage.session_id == sid,
                            ConversationMessage.role == "user",
                        )
                    )
                    .order_by(ConversationMessage.created_at)
                    .limit(1)
                ).scalar()
                title = (first_user_msg or "新对话")[:60]

                results.append({
                    "session_id": sid,
                    "title": title,
                    "message_count": row.message_count,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "last_active": row.last_active.isoformat() if row.last_active else None,
                })
            return results

    def get_conversation_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取单个会话的完整消息列表（按时间正序，用于前端恢复历史）。"""
        with self.session_scope() as session:
            stmt = (
                select(ConversationMessage)
                .where(ConversationMessage.session_id == session_id)
                .order_by(ConversationMessage.created_at)
                .limit(limit)
            )
            messages = session.execute(stmt).scalars().all()
            return [
                {
                    "id": str(msg.id),
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                }
                for msg in messages
            ]

    def delete_conversation_session(self, session_id: str) -> int:
        """
        删除指定会话的所有消息以及其摘要/状态行。

        Returns:
            删除的消息数。
        """
        with self.session_scope() as session:
            result = session.execute(
                delete(ConversationMessage).where(
                    ConversationMessage.session_id == session_id
                )
            )
            session.execute(
                delete(ConversationSummary).where(ConversationSummary.session_id == session_id)
            )
            session.execute(
                delete(ConversationSessionState).where(ConversationSessionState.session_id == session_id)
            )
            return result.rowcount


__all__ = ["ConversationMixin"]
