# -*- coding: utf-8 -*-
"""大盘复盘运行的共享执行锁。

该锁将进程内的本地标志与同主机的锁文件（lock file）结合使用。它防止共享同一
数据目录的 API、CLI、调度器入口并发运行大盘复盘，同时允许在进程崩溃或被强制退出
后清理已失效（stale）的锁文件。
"""

import logging
import errno
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from src.config import Config

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows 平台无 fcntl，走锁文件兜底实现
    fcntl = None


_market_review_lock = threading.Lock()
_market_review_running = False
# 锁文件的过期时限：24 小时，覆盖"进程崩溃后锁文件残留"这一最常见的失效场景
_MARKET_REVIEW_LOCK_STALE_TTL_SECONDS = 24 * 60 * 60
logger = logging.getLogger(__name__)


@dataclass
class MarketReviewExecutionLock:
    """成功获取复盘锁后返回给调用方的令牌（token），用于后续释放。"""

    handle: Any
    path: Path
    uses_flock: bool


def market_review_lock_path(config: Config) -> Path:
    """解析锁文件位置：放置于配置的数据库文件所在目录旁边。"""
    database_path = getattr(config, "database_path", "./data/stock_analysis.db")
    return Path(database_path).parent / "market_review.lock"


def _write_market_review_lock_metadata(handle: Any) -> None:
    """写入 PID 与启动时间，便于诊断与清理失效的锁文件。"""
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\nstarted_at={datetime.now().isoformat()}\n")
    handle.flush()


def _is_process_alive(pid: int) -> bool:
    """判断给定进程 ID 在当前的操作系统平台上是否仍然存活。"""
    if pid <= 0:
        return False

    if os.name == "nt":
        return _is_windows_process_alive(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _is_windows_process_alive(pid: int) -> bool:
    """Windows 平台下探测进程存活情况的实现。"""
    try:
        import ctypes
    except ImportError:  # pragma: no cover - ctypes 属于标准库，正常环境不会触发
        return True

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # 0x1000 = PROCESS_QUERY_LIMITED_INFORMATION，低权限下也能查询进程状态
        process_query_limited_information = 0x1000
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            # 错误码 87（ERROR_INVALID_PARAMETER）通常表示进程已不存在，其余情况保守视为存活
            return ctypes.get_last_error() != 87

        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            # 259 = STILL_ACTIVE，仍在运行即视为锁未失效
            return exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    except Exception as exc:
        logger.warning("Windows 进程存活探测失败，保守视为仍在运行: %s", exc)
        return True


def _read_lock_metadata(lock_path: Path) -> dict[str, str]:
    """尽力读取既有锁文件中的键值（key/value）元数据。"""
    try:
        raw = lock_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            metadata[key.strip()] = value.strip()
    return metadata


def _is_lock_file_expired(lock_path: Path) -> bool:
    """基于锁文件 mtime 的兜底失效判定（fallback stale check）。"""
    try:
        modified_at = datetime.fromtimestamp(lock_path.stat().st_mtime)
    except OSError:
        return False

    return datetime.now() - modified_at > timedelta(
        seconds=_MARKET_REVIEW_LOCK_STALE_TTL_SECONDS
    )


def _is_stale_lock(lock_path: Path) -> bool:
    """判断既有锁文件是否可安全替换（即已失效）。"""
    metadata = _read_lock_metadata(lock_path)
    pid_raw = metadata.get("pid")
    if not pid_raw:
        return _is_lock_file_expired(lock_path)

    try:
        pid = int(pid_raw)
    except ValueError:
        return _is_lock_file_expired(lock_path)

    if not _is_process_alive(pid):
        return True

    started_raw = metadata.get("started_at")
    if not started_raw:
        return False

    try:
        started_at = datetime.fromisoformat(started_raw)
    except ValueError:
        return True

    return datetime.now() - started_at > timedelta(
        seconds=_MARKET_REVIEW_LOCK_STALE_TTL_SECONDS
    )


def try_acquire_market_review_lock(
    config: Config,
) -> Optional[MarketReviewExecutionLock]:
    """获取进程内 + 同主机的市场复盘执行锁。

    该锁结合进程内守卫与文件锁：既阻止同一运行时内 API、CLI、调度器入口的大盘复盘
    相互重叠，也能对共享数据路径的同主机多进程去重。注意它不提供跨主机/容器的去重，
    不适用于多实例部署场景。
    """
    global _market_review_running
    lock_path = market_review_lock_path(config)

    # 进程内守卫标志，先于文件锁生效，避免同进程内多次入口互相竞争
    with _market_review_lock:
        if _market_review_running:
            return None

        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is not None:
            handle = open(lock_path, "a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                handle.close()
                if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in (
                    errno.EACCES,
                    errno.EAGAIN,
                ):
                    return None
                raise
            uses_flock = True
        else:  # pragma: no cover - 仅在缺少 fcntl 的平台（如 Windows）上走此分支
            fd: Optional[int] = None
            # 最多重试一次：先尝试独占创建，失败则判定为失效锁并清理后重试
            for _ in range(2):
                try:
                    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    break
                except FileExistsError:
                    if not _is_stale_lock(lock_path):
                        return None

                    logger.warning("检测到过期的 market_review.lock，尝试清理后重试。")
                    try:
                        lock_path.unlink()
                    except OSError as exc:
                        logger.warning("清理过期 market_review.lock 失败: %s", exc)
                        return None

            if fd is None:
                return None

            handle = os.fdopen(fd, "w+", encoding="utf-8")
            uses_flock = False

        _write_market_review_lock_metadata(handle)
        _market_review_running = True
        return MarketReviewExecutionLock(
            handle=handle,
            path=lock_path,
            uses_flock=uses_flock,
        )


def release_market_review_lock(
    lock_token: Optional[MarketReviewExecutionLock],
) -> None:
    """释放此前获取的大盘复盘锁令牌。"""
    if lock_token is None:
        return

    global _market_review_running
    with _market_review_lock:
        _market_review_running = False

    try:
        if lock_token.uses_flock and fcntl is not None:
            fcntl.flock(lock_token.handle.fileno(), fcntl.LOCK_UN)
    finally:
        # Windows 分支没有 flock 可依赖，锁的存续完全靠文件存在性，必须显式删除
        lock_token.handle.close()
        if not lock_token.uses_flock:
            try:
                lock_token.path.unlink()
            except FileNotFoundError:
                pass
