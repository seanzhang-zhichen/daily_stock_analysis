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
        """将一行物理文本分类，且不丢失其原始内容。
        
        分类规则（按优先级）：
        1. 空行/纯空白 -> kind="blank"
        2. 以 # 开头 -> kind="comment"
        3. 匹配 KEY=VALUE 模式 -> kind="assignment"
        4. 其他 -> kind="raw"（保留原样）
        """
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
        """为归一化后的 key/value 对创建一条"已更新"的赋值行。
        
        标记 updated=True，使 render() 时输出新值而非原始行。
        """
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
        """初始化管理器，绑定当前生效的 env 文件路径。
        
        使用 threading.RLock 保证进程内线程安全，
        但注意：这不是分布式锁，跨进程编辑仍需通过版本号检测冲突。
        """
        self._env_path = env_path or self._resolve_env_path()
        self._lock = threading.RLock()

    @property
    def env_path(self) -> Path:
        """返回当前生效的 ``.env`` 路径。"""
        return self._env_path

    def read_config_map(self) -> Dict[str, str]:
        """从 .env 文件读取键值映射（key/value）。
        
        使用 python-dotenv 的 dotenv_values 解析，
        对无值的键返回空字符串而非 None，保持接口一致性。
        """
        if not self._env_path.exists():
            return {}

        values = dotenv_values(self._env_path)
        return {
            str(key): "" if value is None else str(value)
            for key, value in values.items()
            if key is not None
        }

    def get_config_version(self) -> str:
        """基于文件状态（mtime + 内容哈希）返回确定性的版本字符串。
        
        版本字符串格式：{mtime_ns}:{sha256_hash}
        用于检测跨会话/跨进程的过期编辑（stale edits）。
        """
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
        """将更新写入 .env 文件（尽可能使用原子替换）。

        核心逻辑：
        1. 读取当前配置，与待更新值对比
        2. 敏感字段（如 API Key）若值等于 mask_token，表示"保留当前值"，跳过写入
        3. 无变化的字段跳过，减少不必要的文件写入
        4. 有变化的字段通过 _atomic_upsert 写入
        
        与 mask_token 相等的敏感值表示"保留当前密钥"。将这些字段放入
        skipped_masked 返回，可让 API 解释为何未重写该字段，同时不暴露其真实取值。
        
        Returns:
            (updated_keys, skipped_masked_keys, new_version)
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
        """写入更新：先原子重命名（rename），在挂载文件不支持时回退为原地重写。
        
        原子写入流程：
        1. 读取现有条目，定位每个 key 的最后出现位置
        2. 更新对应条目（或追加新条目）
        3. 写入临时文件 .env.tmp
        4. 调用 os.replace 原子替换原文件
        5. 若 replace 失败（如跨设备/挂载点），回退到原地重写
        """
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
        """当挂载类型不支持重命名时，原地重写 .env 内容。
        
        注意：此操作非原子性，若写入过程中断可能导致文件损坏。
        仅在 os.replace 失败时作为降级方案使用。
        """
        with self._env_path.open("w", encoding="utf-8", newline="\n") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())

    def _read_entries(self) -> List[ConfigLineEntry]:
        """将当前文件读取为可还原的条目列表，保持原有行序。
        
        每行通过 ConfigLineEntry.parse 分类，保留原始内容（包括注释和空白行），
        使后续写入时能还原用户的原始排版和注释。
        """
        if not self._env_path.exists():
            return []
        return [
            ConfigLineEntry.parse(raw_line)
            for raw_line in self._env_path.read_text(encoding="utf-8").splitlines()
        ]

    @staticmethod
    def _find_last_key_indexes(entries: List[ConfigLineEntry]) -> Dict[str, int]:
        """将键映射到其最后一次出现的位置，使重复的环境变量键遵循 dotenv 的覆盖规则。
        
        dotenv 规范中，后出现的同名键覆盖先出现的键。
        本方法确保更新时修改最后一次出现的键，与 dotenv 行为一致。
        """
        key_to_index: Dict[str, int] = {}
        for index, entry in enumerate(entries):
            if entry.kind != "assignment" or entry.key is None:
                continue
            key_to_index[entry.key.upper()] = index

        return key_to_index

    @staticmethod
    def _resolve_env_path() -> Path:
        """从环境变量 ENV_FILE 或仓库根目录解析当前生效的 .env 路径。
        
        优先级：
        1. 环境变量 ENV_FILE（绝对路径或相对路径）
        2. 当前文件所在目录向上回溯 3 层（假设在 backend/src/core/ 下）
        """
        env_file = os.getenv("ENV_FILE")
        if env_file:
            return Path(env_file).resolve()

        return (Path(__file__).resolve().parents[3] / ".env").resolve()
