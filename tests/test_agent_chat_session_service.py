import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.services.agent_chat_session_service import AgentChatSessionService
from src.storage import DatabaseManager


def test_skill_selection_is_persisted_and_reused():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = AgentChatSessionService(db)
    config = SimpleNamespace()
    with patch("src.services.agent_chat_session_service.normalize_requested_skill_ids", return_value=["bull_trend"]):
        first = service.resolve_skill_selection(config, "chat-1", ["bull_trend"])
    assert first.effective_skill_ids == ["bull_trend"]
    service.persist_skill_selection("chat-1", first.selected_skill_ids_update)

    resumed = service.resolve_skill_selection(config, "chat-1", None)
    assert resumed.effective_skill_ids == ["bull_trend"]

    cleared = service.resolve_skill_selection(config, "chat-1", [])
    service.persist_skill_selection("chat-1", cleared.selected_skill_ids_update)
    assert service.resolve_skill_selection(config, "chat-1", None).effective_skill_ids == []
    DatabaseManager.reset_instance()
