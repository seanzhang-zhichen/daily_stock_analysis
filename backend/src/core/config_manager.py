"""Configuration file manager with atomic read/write behavior.

The web settings API updates ``.env`` through this module. It preserves comments
and unknown raw lines where possible, skips masked sensitive values, and uses an
atomic replace with a mounted-file fallback for Docker/Windows environments.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Literal, Optional, Set, Tuple

from dotenv import dotenv_values

_ASSIGNMENT_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_FALLBACK_REWRITE_ERRNOS = {errno.EBUSY, errno.EXDEV}

logger = logging.getLogger(__name__)


@dataclass
class ConfigLineEntry:
    """Structured representation of a single `.env` line.

    ``raw`` lines are intentionally preserved. They allow hand-written content
    that python-dotenv does not parse cleanly to survive a settings save.
    """

    kind: Literal["assignment", "comment", "blank", "raw"]
    raw_line: str
    key: Optional[str] = None
    value: str = ""
    updated: bool = False

    @classmethod
    def parse(cls, raw_line: str) -> "ConfigLineEntry":
        """Classify one physical line without losing its original text."""
        stripped = raw_line.strip()
        if not stripped:
            return cls(kind="blank", raw_line=raw_line)
        if stripped.startswith("#"):
            return cls(kind="comment", raw_line=raw_line)

        matched = _ASSIGNMENT_PATTERN.match(raw_line)
        if matched:
            return cls(
                kind="assignment",
                raw_line=raw_line,
                key=matched.group(1),
                value=matched.group(2),
            )

        return cls(kind="raw", raw_line=raw_line)

    @classmethod
    def assignment(cls, key: str, value: str) -> "ConfigLineEntry":
        """Create an updated assignment line for a normalized key/value pair."""
        return cls(
            kind="assignment",
            raw_line=f"{key}={value}",
            key=key,
            value=value,
            updated=True,
        )

    def render(self) -> str:
        """Render the original line unless this entry was replaced by an update."""
        if self.kind == "assignment" and self.updated and self.key is not None:
            return f"{self.key}={self.value}"
        return self.raw_line


class ConfigManager:
    """Manage `.env` read/write operations with optimistic versioning.

    The class is process-thread-safe, but it is not a distributed lock. API
    callers should still use the returned version string to detect stale edits
    across browser sessions or processes.
    """

    def __init__(self, env_path: Optional[Path] = None):
        """Initialize manager for the active env file path."""
        self._env_path = env_path or self._resolve_env_path()
        self._lock = threading.RLock()

    @property
    def env_path(self) -> Path:
        """Return active `.env` path."""
        return self._env_path

    def read_config_map(self) -> Dict[str, str]:
        """Read key-value mapping from `.env` file."""
        if not self._env_path.exists():
            return {}

        values = dotenv_values(self._env_path)
        return {
            str(key): "" if value is None else str(value)
            for key, value in values.items()
            if key is not None
        }

    def get_config_version(self) -> str:
        """Return deterministic version string based on file state."""
        if not self._env_path.exists():
            return "missing:0"

        content = self._env_path.read_bytes()
        file_stat = self._env_path.stat()
        content_hash = hashlib.sha256(content).hexdigest()
        return f"{file_stat.st_mtime_ns}:{content_hash}"

    def get_updated_at(self) -> Optional[str]:
        """Return `.env` last update time in ISO8601 format."""
        if not self._env_path.exists():
            return None

        file_stat = self._env_path.stat()
        updated_at = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)
        return updated_at.isoformat()

    def apply_updates(
        self,
        updates: Iterable[Tuple[str, str]],
        sensitive_keys: Set[str],
        mask_token: str,
    ) -> Tuple[List[str], List[str], str]:
        """Apply updates into `.env` file using atomic replace when possible.

        Sensitive values equal to ``mask_token`` mean "keep the current secret".
        Returning them in ``skipped_masked`` lets the API explain why those fields
        were not rewritten without exposing the underlying value.
        """
        with self._lock:
            current_values = self.read_config_map()
            mutable_updates: Dict[str, str] = {}
            skipped_masked: List[str] = []

            for key, value in updates:
                key_upper = key.upper()
                current_value = current_values.get(key_upper)

                if key_upper in sensitive_keys and value == mask_token:
                    if current_value not in (None, ""):
                        skipped_masked.append(key_upper)
                    continue

                if current_value == value:
                    continue

                mutable_updates[key_upper] = value

            if mutable_updates:
                self._atomic_upsert(mutable_updates)

            return list(mutable_updates.keys()), skipped_masked, self.get_config_version()

    def _atomic_upsert(self, updates: Dict[str, str]) -> None:
        """Write updates with atomic rename and in-place fallback for mounted files."""
        entries = self._read_entries()
        key_to_index = self._find_last_key_indexes(entries)

        for key, value in updates.items():
            line_value = value.replace("\n", "")
            if key in key_to_index:
                entries[key_to_index[key]] = ConfigLineEntry.assignment(key, line_value)
            else:
                entries.append(ConfigLineEntry.assignment(key, line_value))

        if not self._env_path.parent.exists():
            self._env_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = self._env_path.with_suffix(self._env_path.suffix + ".tmp")
        content = "\n".join(entry.render() for entry in entries)
        if content and not content.endswith("\n"):
            content += "\n"

        with temp_path.open("w", encoding="utf-8", newline="\n") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())

        try:
            os.replace(temp_path, self._env_path)
        except OSError as exc:
            if exc.errno not in _FALLBACK_REWRITE_ERRNOS:
                raise

            logger.warning(
                "Atomic replace for .env failed with errno=%s, falling back to in-place rewrite",
                exc.errno,
            )
            self._rewrite_in_place(content)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _rewrite_in_place(self, content: str) -> None:
        """Rewrite `.env` content in place when rename is unsupported by mount type."""
        with self._env_path.open("w", encoding="utf-8", newline="\n") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())

    def _read_entries(self) -> List[ConfigLineEntry]:
        """Read the current file as renderable entries, preserving line order."""
        if not self._env_path.exists():
            return []
        return [
            ConfigLineEntry.parse(raw_line)
            for raw_line in self._env_path.read_text(encoding="utf-8").splitlines()
        ]

    @staticmethod
    def _find_last_key_indexes(entries: List[ConfigLineEntry]) -> Dict[str, int]:
        """Map keys to their last assignment so duplicate env keys follow dotenv."""
        key_to_index: Dict[str, int] = {}
        for index, entry in enumerate(entries):
            if entry.kind != "assignment" or entry.key is None:
                continue
            key_to_index[entry.key.upper()] = index

        return key_to_index

    @staticmethod
    def _resolve_env_path() -> Path:
        """Resolve the active `.env` path from ENV_FILE or the repository root."""
        env_file = os.getenv("ENV_FILE")
        if env_file:
            return Path(env_file).resolve()

        return (Path(__file__).resolve().parents[3] / ".env").resolve()
