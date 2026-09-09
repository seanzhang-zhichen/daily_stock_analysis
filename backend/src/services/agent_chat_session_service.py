"""Agent 对话请求的会话级（session-level）状态服务。

负责解析并持久化每个对话会话（conversation session）选择的 skill 列表，
供后续 Agent 路由/工具调度按会话复用同一组 skill。

- 与单次请求级别的 skill 解析（见 :func:`src.agent.factory.normalize_requested_skill_ids`）
  配合：前者负责"本次请求带哪些 skill"，后者负责"按 session 记住哪些 skill"
- 仅维护会话维度的 skill 偏好，不参与 LLM 调用或工具执行
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from src.agent.factory import normalize_requested_skill_ids
from src.storage import DatabaseManager


@dataclass(frozen=True)
class ChatSkillSelection:
    """单次请求解析后的 skill 选择结果。

    Attributes:
        effective_skill_ids: 本次实际生效的 skill 列表（含历史值或新值）；
            ``None`` 表示沿用会话已保存的偏好。
        selected_skill_ids_update: 若调用方想更新会话偏好，填入新值；
            ``None`` 表示不更新。
    """

    effective_skill_ids: Optional[List[str]]
    selected_skill_ids_update: Optional[List[str]]


class AgentChatSessionService:
    """解析校验后的用户 skill 选择，并把它们按会话持久化。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """注入数据库管理器；默认走全局 :class:`DatabaseManager` 单例。"""
        self.db = db_manager or DatabaseManager.get_instance()

    def resolve_skill_selection(self, config, session_id: str, requested_skill_ids: Optional[List[str]]) -> ChatSkillSelection:
        """根据请求参数解析本次请求实际生效的 skill 列表。

        行为约定：
        - 未传 ``requested_skill_ids`` → 沿用会话已保存的偏好，``selected_skill_ids_update=None``
        - 传空列表 → 显式清空会话偏好，本请求生效为空列表
        - 传非空列表 → 先用工厂函数校验/规范化，再写回会话
        """
        if requested_skill_ids is None:
            return ChatSkillSelection(self.db.get_conversation_session_selected_skill_ids(session_id), None)
        if not requested_skill_ids:
            return ChatSkillSelection([], [])
        normalized = normalize_requested_skill_ids(config, requested_skill_ids)
        # 规范化后全部非法 → 视为"未指定"，沿用历史偏好且不更新
        if not normalized:
            return ChatSkillSelection(self.db.get_conversation_session_selected_skill_ids(session_id), None)
        return ChatSkillSelection(normalized, normalized)

    def persist_skill_selection(self, session_id: str, skill_ids: Optional[List[str]]) -> None:
        """把新选择的 skill 列表写入会话偏好；``None`` 表示保持不动。"""
        if skill_ids is not None:
            self.db.save_conversation_session_selected_skill_ids(session_id, skill_ids)


__all__ = ["AgentChatSessionService", "ChatSkillSelection"]
