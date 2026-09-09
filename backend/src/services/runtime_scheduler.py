"""面向长生命周期 C 端 API 进程的隔离调度器。

CLI 以 ``--schedule`` 启动时自带调度能力；API/Web/Desktop 进程改为使用本服务：
每次 A 股用户定时分析都在独立子进程中执行，避免不配合的上游行情服务或 LLM
调用把 Web 服务器长时间卡死。

设计要点：
- 通过 ``spawn`` 多进程上下文（multiprocessing spawn）启动子进程，避免 fork 方式
  携带父进程锁/线程/文件描述符等副作用
- 子进程与主进程通过 :class:`multiprocessing.Queue` 汇报终态；
  看门狗线程负责超时回收
- Windows 下用 ``taskkill /T /F`` 终止整棵进程树，POSIX 下退化为
  ``process.terminate()`` / ``process.kill()`` 二段式回收
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import subprocess
import threading
import time
from datetime import date, datetime
from queue import Empty
from types import SimpleNamespace
from typing import Any, Optional

from src.config import get_config

logger = logging.getLogger(__name__)
# 当 CLI 已经以 --schedule 启动并持有调度权时，子进程内的调度器需主动让位
CLI_SCHEDULER_OWNER_ENV = "DSA_CLI_SCHEDULER_OWNS_SCHEDULE"
# 单次定时分析默认超时 45 分钟：覆盖完整 LLM 报告生成 + 多轮数据抓取
DEFAULT_TIMEOUT_SECONDS = 45 * 60


def _run_user_schedule_child(result_queue: Any) -> None:
    """子进程入口：执行一次 C 端每日 A 股分析，并通过队列向父进程汇报终态。"""
    try:
        # 兼容两种包布局（仓库根 / backend/ 目录）；任一可用即可
        try:
            from main import run_per_user_scheduled_analysis
        except ImportError:
            from backend.main import run_per_user_scheduled_analysis
        from src.users.config import is_user_mode_enabled

        if not is_user_mode_enabled():
            result_queue.put({"success": True, "skipped": "user_mode_disabled"})
            return
        # workers/dry_run/force_run 当前不开放给定时任务入口，由各服务自行决定并发
        args = SimpleNamespace(workers=None, dry_run=False, force_run=False)
        run_per_user_scheduled_analysis(get_config(), args)
        result_queue.put({"success": True})
    except BaseException as exc:  # child must always report its terminal state
        # 兜底捕获 BaseException：子进程必须把终止状态汇报给父进程，否则看门狗会一直等待
        result_queue.put({"success": False, "error": str(exc)})


def _terminate_process_tree(process: multiprocessing.Process) -> None:
    """回收超时的子进程；Windows 上递归终止整棵进程树，POSIX 走 ``terminate`` → ``kill``。"""
    if not process.is_alive():
        return
    try:
        if os.name == "nt" and process.pid:
            # Windows：``taskkill /T /F`` 可一并杀掉子进程派生的孙进程
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            process.terminate()
    except (OSError, subprocess.SubprocessError):
        # 兜底：极端情况下 taskkill/terminate 失败，尝试 kill 强行回收
        process.terminate()
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join(5)


class RuntimeSchedulerService:
    """为长生命周期 API 进程提供"每天一次"、隔离子进程的 C 端 A 股定时分析调度。"""

    def __init__(self) -> None:
        """初始化调度器内部状态（线程、锁、最近一次执行元信息）。"""
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # 互斥锁防止上一轮分析尚未结束时，又被同日重复触发
        self._run_lock = threading.Lock()
        self._watchdog_start_lock = threading.Lock()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._last_run_date: Optional[date] = None
        self._last_error: Optional[str] = None
        self._last_success_at: Optional[str] = None
        self._last_run_at: Optional[str] = None

    @staticmethod
    def _schedule_time(config: Any) -> Optional[str]:
        """读取 ``SCHEDULE_TIME``（HH:MM）；格式非法时返回 ``None`` 表示当天跳过。"""
        value = str(getattr(config, "schedule_time", "") or "").strip()
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            logger.warning("Runtime scheduler ignored invalid SCHEDULE_TIME=%r", value)
            return None
        return value

    def _timeout_seconds(self) -> int:
        """返回单次分析的超时秒数，最小 60s；非法配置回落到 45 分钟默认。"""
        value = getattr(get_config(), "runtime_scheduler_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        try:
            return max(60, int(value))
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_SECONDS

    def _run_with_watchdog(self) -> None:
        """看门狗主体：派生子进程执行分析，并通过队列等待结果；超时则回收进程树。"""
        if not self._run_lock.acquire(blocking=False):
            # 非阻塞加锁失败 → 上一轮分析尚未结束；直接跳过避免叠加
            logger.warning("Runtime scheduler skipped overlapping analysis run")
            return
        result_queue = None
        process = None
        try:
            # spawn：避免 fork 方式携带父进程的锁、线程、连接等副作用
            context = multiprocessing.get_context("spawn")
            result_queue = context.Queue()
            process = context.Process(
                target=_run_user_schedule_child,
                args=(result_queue,),
                name="c-end-scheduled-a-share-analysis",
            )
            process.start()
            self._last_run_at = datetime.now().isoformat()
            timeout = self._timeout_seconds()
            try:
                result = result_queue.get(timeout=timeout)
            except Empty:
                # 超时：回收子进程并把超时信息记录到 _last_error
                _terminate_process_tree(process)
                self._last_error = f"scheduled A-share analysis timed out after {timeout}s"
                logger.error(self._last_error)
                return
            process.join(5)
            if process.is_alive():
                # 子进程未在队列写入后及时退出，强制清理
                _terminate_process_tree(process)
            if result.get("success"):
                self._last_success_at = datetime.now().isoformat()
                self._last_error = None
            else:
                self._last_error = str(result.get("error") or "scheduled A-share analysis failed")
                logger.error("Runtime scheduler worker failed: %s", self._last_error)
        except Exception as exc:  # scheduler errors must not stop FastAPI
            # 看门狗层异常也不能让 FastAPI 崩溃；只记录到 _last_error
            self._last_error = str(exc)
            logger.exception("Runtime scheduler watchdog failed")
        finally:
            if result_queue is not None:
                # cancel_join_thread 防止进程退出时阻塞在队列 join 上
                result_queue.cancel_join_thread()
                result_queue.close()
            self._run_lock.release()

    def _start_watchdog(self) -> bool:
        """在新 daemon 线程中启动一次看门狗执行（短生命周期）。"""
        if not self._watchdog_start_lock.acquire(blocking=False):
            logger.warning("Runtime scheduler skipped overlapping watchdog start")
            return False

        def _run_and_release() -> None:
            try:
                self._run_with_watchdog()
            finally:
                self._watchdog_start_lock.release()

        self._watchdog_thread = threading.Thread(
            target=_run_and_release,
            name="runtime-scheduler-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()
        return True

    def run_now(self) -> dict[str, Any]:
        """Request one isolated run without waiting for its analysis process."""
        accepted = self._start_watchdog()
        return {
            "accepted": accepted,
            "running": bool(self._watchdog_start_lock.locked()),
            "reason": None if accepted else "analysis_already_running",
        }

    def _loop(self) -> None:
        """调度主循环：每隔 15s 检查是否到达当日计划时间；命中即触发看门狗。"""
        # 配置了"立即执行"时启动即先跑一次，方便开发/冒烟
        if bool(getattr(get_config(), "schedule_run_immediately", False)):
            self._last_run_date = date.today()
            self._start_watchdog()
        while not self._stop_event.wait(15):
            config = get_config()
            if not bool(getattr(config, "schedule_enabled", False)):
                continue
            schedule_time = self._schedule_time(config)
            now = datetime.now()
            # 每分钟最多触发一次；用 _last_run_date 防同日重复执行
            if schedule_time and now.strftime("%H:%M") == schedule_time and self._last_run_date != now.date():
                self._last_run_date = now.date()
                self._start_watchdog()

    def start(self) -> None:
        """启动后台调度线程；CLI 已持有调度权或全局开关关闭时直接返回。"""
        # 让位给 CLI 端的调度器，避免与 ``--schedule`` 重复触发
        if os.getenv(CLI_SCHEDULER_OWNER_ENV, "").lower() in {"1", "true", "yes"}:
            return
        if not bool(getattr(get_config(), "schedule_enabled", False)):
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="runtime-scheduler", daemon=True)
        self._thread.start()
        logger.info("Runtime scheduler started with isolated A-share worker")

    def stop(self) -> None:
        """通知后台循环退出，并等待调度线程结束（最多 2 秒）。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def status(self) -> dict[str, Optional[str] | bool]:
        """返回调度器当前运行状态与最近一次执行的元信息，供 API/CLI 暴露。"""
        return {
            "enabled": bool(getattr(get_config(), "schedule_enabled", False)),
            "running": bool(self._thread and self._thread.is_alive()),
            "worker_running": bool(self._watchdog_start_lock.locked()),
            "last_run_at": self._last_run_at,
            "last_success_at": self._last_success_at,
            "last_error": self._last_error,
        }
