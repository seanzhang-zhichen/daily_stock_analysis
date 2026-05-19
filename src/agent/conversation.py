# -*- coding: utf-8 -*-
"""
Conversation Manager for Agent multi-turn chat.

Manages conversation sessions with TTL, storing message history and context.
"""

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.storage import get_db

logger = logging.getLogger(__name__)


# To C 多用户隔离: Web endpoint 会把 session_id 加上 ``u{user_id}:`` 前缀,
# 此处反解出 user_id 以便把消息写到该用户名下; Bot / CLI 路径保持原 session_id
# (例如 ``telegram_xxx``), 解析失败回退到 None, 行为等价于单租户模式。
_USER_SESSION_PREFIX_RE = re.compile(r"^u(\d+):")


def extract_user_id_from_session(session_id: Optional[str]) -> Optional[int]:
    """从 ``u{user_id}:...`` 前缀里反解 user_id; 不匹配返回 ``None``。"""
    if not session_id:
        return None
    match = _USER_SESSION_PREFIX_RE.match(session_id)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


@dataclass
class ConversationSession:
    """A single multi-turn conversation session."""
    session_id: str
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)

    def add_message(self, role: str, content: str):
        """Add a message to the session history."""
        user_id = extract_user_id_from_session(self.session_id)
        get_db().save_conversation_message(
            self.session_id, role, content, user_id=user_id,
        )
        self.last_active = datetime.now()

    def update_context(self, key: str, value: Any):
        """Update session context."""
        self.context[key] = value
        self.last_active = datetime.now()

    def get_history(self) -> List[Dict[str, Any]]:
        """Get message history."""
        messages = get_db().get_conversation_history(self.session_id)
        return messages

class ConversationManager:
    """Manages multiple conversation sessions with TTL."""
    
    def __init__(self, ttl_minutes: int = 30):
        self._sessions: Dict[str, ConversationSession] = {}
        self.ttl = timedelta(minutes=ttl_minutes)
        self._lock = threading.RLock()

    def get_or_create(self, session_id: str) -> ConversationSession:
        """Get an existing session or create a new one."""
        with self._lock:
            self._cleanup_expired()

            if session_id not in self._sessions:
                self._sessions[session_id] = ConversationSession(session_id=session_id)
                logger.info(f"Created new conversation session: {session_id}")
            else:
                # Update last active time
                self._sessions[session_id].last_active = datetime.now()

            return self._sessions[session_id]

    def add_message(self, session_id: str, role: str, content: str):
        """Add a message to a session."""
        session = self.get_or_create(session_id)
        session.add_message(role, content)

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Get message history for a session."""
        session = self.get_or_create(session_id)
        return session.get_history()

    def clear(self, session_id: str):
        """Clear a session."""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info(f"Cleared conversation session: {session_id}")
        # We don't delete from DB here to keep history, or we could add a delete method.
        # For now, just clear from memory.

    def _cleanup_expired(self):
        """Remove expired sessions."""
        with self._lock:
            now = datetime.now()
            expired = [
                sid for sid, session in self._sessions.items()
                if now - session.last_active > self.ttl
            ]
            for sid in expired:
                del self._sessions[sid]
                logger.info(f"Cleaned up expired conversation session: {sid}")

# Global instance
conversation_manager = ConversationManager()
