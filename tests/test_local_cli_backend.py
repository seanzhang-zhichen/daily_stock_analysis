import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.llm.generation_backend import GenerationError, GenerationErrorCode
from src.llm.local_cli_backend import LocalCliGenerationBackend
from src.llm.backend_registry import resolve_generation_backend_id


def _config(**values):
    return SimpleNamespace(generation_backend_timeout_seconds=5, generation_backend_max_output_bytes=1024,
                           litellm_model="", opencode_cli_model="", **values)


def test_codex_cli_uses_fixed_non_shell_command_and_allowlisted_environment():
    backend = LocalCliGenerationBackend("codex_cli", _config())
    completed = SimpleNamespace(stdout="report", stderr="", returncode=0)
    with patch("src.llm.local_cli_backend.shutil.which", return_value="C:/bin/codex"), patch(
        "src.llm.local_cli_backend.subprocess.run", return_value=completed
    ) as run:
        result = backend.generate("prompt", {})

    assert result.text == "report"
    command = run.call_args.args[0]
    assert command[0] == "codex"
    assert "--sandbox" in command and "read-only" in command
    assert run.call_args.kwargs.get("shell", False) is False
    assert "OPENAI_API_KEY" not in run.call_args.kwargs["env"]


def test_missing_cli_is_fallbackable_error():
    backend = LocalCliGenerationBackend("opencode_cli", _config())
    with patch("src.llm.local_cli_backend.shutil.which", return_value=None):
        with pytest.raises(GenerationError) as exc:
            backend.generate("prompt", {})
    assert exc.value.error_code == GenerationErrorCode.COMMAND_NOT_FOUND
    assert exc.value.fallbackable is True


def test_missing_optional_backend_field_keeps_legacy_litellm_default():
    assert resolve_generation_backend_id(object()) == "litellm"
