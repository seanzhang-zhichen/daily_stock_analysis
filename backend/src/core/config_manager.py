"""配置文件管理器（原子读写语义）。

Web 设置 API 通过本模块更新 ``.env`` 文件。它尽可能保留注释与无法识别的原始行，
跳过被掩码的敏感值，并在 Docker/Windows 环境下用原子替换（atomic replace）配合
挂载文件兜底（in-place fallback）完成写入。
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
    """单条 ``.env`` 行的结构化表示。

    原始行（``raw``）会被刻意保留，使 python-dotenv 无法干净解析的手写内容在设置保存
    后依然得以留存，避免破坏用户原有注释与排版。
    """

    kind: Literal["assignment", "comment", "blank", "raw"]
    raw_line: str
    key: Optional[str] = None
    value: str = ""
    updated: bool = False

    @classmethod
    def parse(cls, raw_line: str) -> "ConfigLineEntry":
        """将一行物理文本分类，且不丢失其原始内容。"""
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
        """为归一化后的 key/value 对创建一条"已更新"的赋值行。"""
        return cls(
            kind="assignment",
            raw_line=f"{key}={value}",
            key=key,
            value=value,
            updated=True,
        )

    def render(self) -> str:
        """还原该行的文本；除非本条已被更新替换，否则返回原始行。"""
        if self.kind == "assignment" and self.updated and self.key is not None:
            return f"{self.key}={self.value}"
        return self.raw_line


class ConfigManager:
    """管理 ``.env`` 的读写操作，采用乐观版本（optimistic versioning）机制。

    该类是进程内线程安全的，但并非分布式锁。API 调用方仍应使用返回的版本字符串来
    检测跨浏览器会话或跨进程的过期编辑（stale edits）。
    """

    def __init__(self, env_path: Optional[Path] = None):
        """初始化管理器，绑定当前生效的 env 文件路径。"""
        self._env_path = env_path or self._resolve_env_path()
        self._lock = threading.RLock()

    @property
    def env_path(self) -> Path:
        """返回当前生效的 ``.env`` 路径。"""
        return self._env_path

    def read_config_map(self) -> Dict[str, str]:
        """从 ``.env`` 文件读取键值映射（key/value）。"""
        if not self._env_path.exists():
            return {}

        values = dotenv_values(self._env_path)
        return {
            str(key): "" if value is None else str(value)
            for key, value in values.items()
            if key is not None
        }

    def get_config_version(self) -> str:
        """基于文件状态（mtime + 内容哈希）返回确定性的版本字符串。"""
        if not self._env_path.exists():
            return "missing:0"

        content = self._env_path.read_bytes()
        file_stat = self._env_path.stat()
        content_hash = hashlib.sha256(content).hexdigest()
        return f"{file_stat.st_mtime_ns}:{content_hash}"

    def get_updated_at(self) -> Optional[str]:
        """返回 ``.env`` 最后更新时间（ISO8601 格式）。"""
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
        """将更新写入 ``.env`` 文件（尽可能使用原子替换）。

        与 ``mask_token`` 相等的敏感值表示"保留当前密钥"。将这些字段放入
        ``skipped_masked`` 返回，可让 API 解释为何未重写该字段，同时不暴露其真实取值。
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
        """写入更新：先原子重命名（rename），在挂载文件不支持时回退为原地重写。"""
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
        """当挂载类型不支持重命名时，原地重写 ``.env`` 内容。"""
        with self._env_path.open("w", encoding="utf-8", newline="\n") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())

    def _read_entries(self) -> List[ConfigLineEntry]:
        """将当前文件读取为可还原的条目列表，保持原有行序。"""
        if not self._env_path.exists():
            return []
        return [
            ConfigLineEntry.parse(raw_line)
            for raw_line in self._env_path.read_text(encoding="utf-8").splitlines()
        ]

    @staticmethod
    def _find_last_key_indexes(entries: List[ConfigLineEntry]) -> Dict[str, int]:
        """将键映射到其最后一次出现的位置，使重复的环境变量键遵循 dotenv 的覆盖规则。"""
        key_to_index: Dict[str, int] = {}
        for index, entry in enumerate(entries):
            if entry.kind != "assignment" or entry.key is None:
                continue
            key_to_index[entry.key.upper()] = index

        return key_to_index

    @staticmethod
    def _resolve_env_path() -> Path:
        """从环境变量 ENV_FILE 或仓库根目录解析当前生效的 ``.env`` 路径。"""
        env_file = os.getenv("ENV_FILE")
        if env_file:
            return Path(env_file).resolve()

        return (Path(__file__).resolve().parents[3] / ".env").resolve()
