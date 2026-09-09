import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.agent.chat_context import build_chat_history
from src.config import get_agent_context_compression_preset
from src.agent.llm_adapter import LLMResponse
from src.storage import DatabaseManager


def test_chat_context_compresses_and_preserves_tail():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    for i in range(8):
        db.save_conversation_message("s1", "user", f"用户问题 {i} " + "x" * 80)
        db.save_conversation_message("s1", "assistant", f"回答 {i} " + "y" * 80)
    adapter = MagicMock()
    adapter.call_text.return_value = LLMResponse(content="## 会话摘要\n已讨论 A 股标的", provider="mock")
    config = MagicMock(
        agent_context_compression_enabled=True,
        agent_context_compression_trigger_tokens=100,
        agent_context_protected_turns=2,
        agent_context_compression_summary_tokens=200,
    )

    history = build_chat_history("s1", adapter, config)

    assert history[0]["content"].startswith("[系统生成的历史对话摘要")
    assert history[-1]["content"].startswith("回答 7")
    saved = db.get_conversation_summary("s1")
    assert saved and saved["covered_message_id"] > 0
    DatabaseManager.reset_instance()


def test_context_compression_profiles_have_stable_defaults():
    assert get_agent_context_compression_preset("cost").trigger_tokens == 6000
    assert get_agent_context_compression_preset("balanced").protected_turns == 4
    assert get_agent_context_compression_preset("long_context_raw_first").summary_tokens == 2600
    assert get_agent_context_compression_preset("unknown").trigger_tokens == 12000
