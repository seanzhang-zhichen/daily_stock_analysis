# -*- coding: utf-8 -*-
"""进程内异步分析任务队列。

负责 API 任务的完整生命周期：

- 单例队列（同一进程内全局共享）；
- 同标的（同一用户）去重：阻止重复分析同一只股票；
- 线程池执行分析任务；
- SSE 事件广播给前端订阅者；
- 任务完成后自动清理过期历史；
- 任务失败时按配置 best-effort 退还当日配额 / 积分。

注意：本队列是单进程内的队列，不在多进程之间协调。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional, Dict, List, Any, TYPE_CHECKING, Tuple, Literal, Callable

if TYPE_CHECKING:
    from asyncio import Queue as AsyncQueue

from data_provider.base import canonical_stock_code, normalize_stock_code
from src.utils.analysis_metadata import SELECTION_SOURCES

logger = logging.getLogger(__name__)


def _dedupe_stock_code_key(stock_code: str) -> str:
    """
    生成股票代码的去重 key。

    把 ``600519`` / ``600519.SH`` / ``600519.SH`` 等视为同一标的；
    对指数类代码则使用 canonical_id（casefold 后的大小写无关形式）。
    """
    raw = (stock_code or "").strip()
    try:
        from src.services.stock_list_parser import ParseStatus, parse_analysis_target
        target = parse_analysis_target(raw)
        if target.status == ParseStatus.INDEX:
            return target.canonical_id.casefold()
    except Exception:
        pass
    return canonical_stock_code(normalize_stock_code(raw))


def _dedupe_task_key(stock_code: str, user_id: Optional[int] = None) -> str:
    """按用户隔离的去重 key：ToC 模式下不同用户的同名任务不互斥。"""
    owner = f"user:{int(user_id)}" if user_id is not None else "global"
    return f"{owner}:{_dedupe_stock_code_key(stock_code)}"


class TaskStatus(str, Enum):
    """任务状态枚举。"""
    PENDING = "pending"        # 等待执行
    PROCESSING = "processing"  # 处理中
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"          # 已失败


@dataclass
class TaskInfo:
    """
    任务信息数据类。

    用于 API 响应和内部任务管理。
    """
    task_id: str
    stock_code: str
    stock_name: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    report_type: str = "detailed"
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    original_query: Optional[str] = None
    selection_source: Optional[str] = None
    query_source: str = "api"
    analysis_phase: str = "auto"
    # 分析 worker 的内部上下文；刻意不暴露在 to_dict 中，避免持仓信息泄漏到 API/SSE
    portfolio_context: Optional[Dict[str, Any]] = None
    skills: Optional[List[str]] = None
    # To C 模式下的归属用户 ID
    user_id: Optional[int] = None
    refund_analysis_quota: bool = False
    quota_refund_date: Optional[date] = None
    refund_analysis_credits: bool = False
    analysis_credit_cost: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """把任务信息转为 API 友好的字典（不含内部 portfolio_context）。"""
        return {
            "task_id": self.task_id,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "report_type": self.report_type,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "original_query": self.original_query,
            "selection_source": self.selection_source,
            "query_source": self.query_source,
            "analysis_phase": self.analysis_phase,
            "skills": self.skills,
        }

    def copy(self) -> 'TaskInfo':
        """创建任务的浅拷贝（含列表/字典的可副本），供跨线程传递。"""
        return TaskInfo(
            task_id=self.task_id,
            stock_code=self.stock_code,
            stock_name=self.stock_name,
            status=self.status,
            progress=self.progress,
            message=self.message,
            result=self.result,
            error=self.error,
            report_type=self.report_type,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            original_query=self.original_query,
            selection_source=self.selection_source,
            query_source=self.query_source,
            analysis_phase=self.analysis_phase,
            portfolio_context=dict(self.portfolio_context) if isinstance(self.portfolio_context, dict) else None,
            skills=list(self.skills) if self.skills is not None else None,
            user_id=self.user_id,
            refund_analysis_quota=self.refund_analysis_quota,
            quota_refund_date=self.quota_refund_date,
            refund_analysis_credits=self.refund_analysis_credits,
            analysis_credit_cost=self.analysis_credit_cost,
        )


class DuplicateTaskError(Exception):
    """
    重复提交异常。

    当股票已在分析中时抛出此异常，携带已有的 task_id 方便调用方给前端展示。
    """
    def __init__(self, stock_code: str, existing_task_id: str):
        """记录冲突的 stock/task 对，供 API 错误响应使用。"""
        self.stock_code = stock_code
        self.existing_task_id = existing_task_id
        super().__init__(f"股票 {stock_code} 正在分析中 (task_id: {existing_task_id})")


class AnalysisTaskQueue:
    """
    异步分析任务队列。

    单例模式，进程内全局唯一实例。

    特性：
    1. 防止相同股票代码重复提交（按用户隔离）；
    2. 线程池执行分析任务；
    3. SSE 事件广播机制；
    4. 任务完成后自动持久化与历史清理；
    5. 任务失败时按配置退还当日配额 / 积分。
    """

    _instance: Optional['AnalysisTaskQueue'] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """创建进程级的单例队列实例。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, max_workers: int = 3):
        """初始化队列状态；多次构造只生效一次（singleton 友好）。"""
        # 防止重复初始化
        if hasattr(self, '_initialized') and self._initialized:
            return

        self._max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = None

        # 核心数据结构
        self._tasks: Dict[str, TaskInfo] = {}           # task_id -> TaskInfo
        self._analyzing_stocks: Dict[str, str] = {}     # dedupe_key -> task_id
        self._futures: Dict[str, Future] = {}           # task_id -> Future

        # SSE 订阅者列表（asyncio.Queue 实例）
        self._subscribers: List[Tuple['AsyncQueue', Optional[int]]] = []
        self._subscribers_lock = threading.Lock()

        # 主事件循环引用（用于跨线程广播）
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        # 线程安全锁
        self._data_lock = threading.RLock()

        # 任务历史保留数量（内存中）
        self._max_history = 100

        self._initialized = True
        logger.info(f"[TaskQueue] 初始化完成，最大并发: {max_workers}")

    @property
    def executor(self) -> ThreadPoolExecutor:
        """懒加载线程池：第一次访问时才创建 ThreadPoolExecutor。"""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="analysis_task_"
            )
        return self._executor

    @property
    def max_workers(self) -> int:
        """返回当前的并发上限设置。"""
        return self._max_workers

    def _has_inflight_tasks_locked(self) -> bool:
        """判断队列内是否还有 pending / processing 任务（必须持锁调用）。"""
        if self._analyzing_stocks:
            return True
        return any(
            task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)
            for task in self._tasks.values()
        )

    def sync_max_workers(
        self,
        max_workers: int,
        *,
        log: bool = True,
    ) -> Literal["applied", "unchanged", "deferred_busy"]:
        """
        在不替换单例的前提下尝试同步并发数。

        Returns:
            - "applied": 新值已生效（仅当队列空闲）
            - "unchanged": 与当前值相同或非法
            - "deferred_busy": 队列繁忙，已延后到下次同步
        """
        try:
            target = max(1, int(max_workers))
        except (TypeError, ValueError):
            if log:
                logger.warning("[TaskQueue] 忽略非法 MAX_WORKERS 值: %r", max_workers)
            return "unchanged"

        executor_to_shutdown: Optional[ThreadPoolExecutor] = None
        previous: int
        with self._data_lock:
            previous = self._max_workers
            if target == previous:
                return "unchanged"

            # 队列繁忙时不允许热调整：避免正在执行的任务被丢弃
            if self._has_inflight_tasks_locked():
                if log:
                    logger.info(
                        "[TaskQueue] 最大并发调整延后: 当前繁忙 (%s -> %s)",
                        previous,
                        target,
                    )
                return "deferred_busy"

            self._max_workers = target
            executor_to_shutdown = self._executor
            self._executor = None

        if executor_to_shutdown is not None:
            executor_to_shutdown.shutdown(wait=False)

        if log:
            logger.info("[TaskQueue] 最大并发已更新: %s -> %s", previous, target)
        return "applied"
    
    # ========== 任务提交与查询 ==========
    
    def is_analyzing(self, stock_code: str, user_id: Optional[int] = None) -> bool:
        """
        检查股票是否正在分析中。

        Args:
            stock_code: 股票代码
            user_id: ToC 模式下的归属用户 ID

        Returns:
            True 表示正在分析中
        """
        dedupe_key = _dedupe_task_key(stock_code, user_id)
        with self._data_lock:
            return dedupe_key in self._analyzing_stocks

    def get_analyzing_task_id(self, stock_code: str, user_id: Optional[int] = None) -> Optional[str]:
        """
        获取正在分析该股票的任务 ID。

        Args:
            stock_code: 股票代码
            user_id: ToC 模式下的归属用户 ID

        Returns:
            任务 ID；如果没有则返回 None
        """
        dedupe_key = _dedupe_task_key(stock_code, user_id)
        with self._data_lock:
            return self._analyzing_stocks.get(dedupe_key)

    def validate_selection_source(self, selection_source: Optional[str]) -> None:
        """校验 selection_source 是否在白名单内，不在则抛 ValueError。"""
        if selection_source is not None and selection_source not in SELECTION_SOURCES:
            raise ValueError(
                f"Invalid selection_source: {selection_source}. "
                f"Must be one of {SELECTION_SOURCES}"
            )

    def submit_task(
        self,
        stock_code: str,
        stock_name: Optional[str] = None,
        original_query: Optional[str] = None,
        selection_source: Optional[str] = None,
        report_type: str = "detailed",
        force_refresh: bool = False,
        skills: Optional[List[str]] = None,
        user_id: Optional[int] = None,
        query_source: str = "api",
        analysis_phase: str = "auto",
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> TaskInfo:
        """
        提交一个单只股票的分析任务。

        Args:
            stock_code: 股票代码
            stock_name: 可选的股票名称
            original_query: 原始用户输入
            selection_source: 来源标签
            report_type: 报告类型
            force_refresh: 是否绕过缓存
            user_id: ToC 模式下的归属用户 ID

        Returns:
            TaskInfo：已接受的任务信息

        Raises:
            DuplicateTaskError: 股票已在分析中
        """
        stock_code = canonical_stock_code(stock_code)
        if not stock_code:
            raise ValueError("股票代码不能为空或仅包含空白字符")

        accepted, duplicates = self.submit_tasks_batch(
            [stock_code],
            stock_name=stock_name,
            original_query=original_query,
            selection_source=selection_source,
            query_source=query_source,
            portfolio_context=portfolio_context,
            report_type=report_type,
            analysis_phase=analysis_phase,
            force_refresh=force_refresh,
            skills=skills,
            user_id=user_id,
        )
        if duplicates:
            raise duplicates[0]
        return accepted[0]

    def submit_tasks_batch(
        self,
        stock_codes: List[str],
        stock_name: Optional[str] = None,
        original_query: Optional[str] = None,
        selection_source: Optional[str] = None,
        report_type: str = "detailed",
        force_refresh: bool = False,
        notify: bool = True,
        skills: Optional[List[str]] = None,
        user_id: Optional[int] = None,
        refund_analysis_quota: bool = False,
        quota_refund_date: Optional[date] = None,
        refund_analysis_credits: bool = False,
        analysis_credit_cost: int = 0,
        query_source: str = "api",
        analysis_phase: str = "auto",
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[TaskInfo], List[DuplicateTaskError]]:
        """
        批量提交分析任务。

        - 重复股票跳过并收集到 ``duplicates``；
        - 线程池提交失败则整批回滚，保证不会出现"半提交"状态；
        - 所有提交成功后才统一广播 ``task_created`` 事件，确保前端看到一致状态。
        """
        self.validate_selection_source(selection_source)

        accepted: List[TaskInfo] = []
        duplicates: List[DuplicateTaskError] = []
        created_task_ids: List[str] = []

        canonical_codes = []
        for code in stock_codes:
            raw = (code or "").strip()
            try:
                from src.services.stock_list_parser import ParseStatus, parse_analysis_target
                target = parse_analysis_target(raw)
                normalized = target.canonical_id if target.status == ParseStatus.INDEX else canonical_stock_code(code)
            except Exception:
                normalized = canonical_stock_code(code)
            if normalized:
                canonical_codes.append(normalized)

        with self._data_lock:
            for stock_code in canonical_codes:
                dedupe_key = _dedupe_task_key(stock_code, user_id)
                if dedupe_key in self._analyzing_stocks:
                    existing_task_id = self._analyzing_stocks[dedupe_key]
                    duplicates.append(DuplicateTaskError(stock_code, existing_task_id))
                    continue

                task_id = uuid.uuid4().hex
                task_skills = list(skills) if skills is not None else None
                task_info = TaskInfo(
                    task_id=task_id,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    status=TaskStatus.PENDING,
                    message="任务已加入队列",
                    report_type=report_type,
                    original_query=original_query,
                    selection_source=selection_source,
                    query_source=query_source or "api",
                    analysis_phase=analysis_phase or "auto",
                    portfolio_context=dict(portfolio_context) if isinstance(portfolio_context, dict) else None,
                    skills=task_skills,
                    user_id=user_id,
                    refund_analysis_quota=bool(refund_analysis_quota),
                    quota_refund_date=quota_refund_date,
                    refund_analysis_credits=bool(refund_analysis_credits),
                    analysis_credit_cost=max(0, int(analysis_credit_cost or 0)),
                )
                self._tasks[task_id] = task_info
                self._analyzing_stocks[dedupe_key] = task_id

                try:
                    future = self.executor.submit(
                        self._execute_task,
                        task_id,
                        stock_code,
                        report_type,
                        force_refresh,
                        notify,
                        task_skills,
                        user_id=user_id,
                        refund_analysis_quota=bool(refund_analysis_quota),
                        quota_refund_date=quota_refund_date,
                        refund_analysis_credits=bool(refund_analysis_credits),
                        analysis_credit_cost=max(0, int(analysis_credit_cost or 0)),
                    )
                except Exception:
                    # Roll back the current batch to avoid partial submission.
                    self._rollback_submitted_tasks_locked(created_task_ids + [task_id])
                    raise

                self._futures[task_id] = future
                accepted.append(task_info)
                created_task_ids.append(task_id)
                logger.info(f"[TaskQueue] 任务已提交: {stock_code} -> {task_id}")

            # Keep task_created ordered before worker-emitted task_started/task_completed.
            # Broadcasting here also preserves batch rollback semantics because we only
            # reach this point after every submit in the batch has succeeded.
            for task_info in accepted:
                self._broadcast_event("task_created", task_info.to_dict(), user_id=task_info.user_id)

        return accepted, duplicates

    def submit_background_task(
        self,
        run_task: Callable[[], Optional[Any]],
        *,
        stock_code: str,
        stock_name: Optional[str] = None,
        report_type: str = "detailed",
        message: Optional[str] = "任务已加入队列",
        task_id: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> TaskInfo:
        """提交一个通用后台可调用对象，跟踪其生命周期但不感知具体分析流程。"""
        task_id = task_id or uuid.uuid4().hex
        task_info = TaskInfo(
            task_id=task_id,
            stock_code=stock_code,
            stock_name=stock_name,
            status=TaskStatus.PENDING,
            message=message,
            report_type=report_type,
            user_id=user_id,
        )

        with self._data_lock:
            if task_id in self._tasks:
                raise ValueError(f"任务 ID 已存在: {task_id}")
            self._tasks[task_id] = task_info
            try:
                future = self.executor.submit(self._execute_background_task, task_id, run_task)
            except Exception:
                del self._tasks[task_id]
                raise

            self._futures[task_id] = future
            self._broadcast_event("task_created", task_info.to_dict(), user_id=task_info.user_id)

        return task_info.copy()

    def _rollback_submitted_tasks_locked(self, task_ids: List[str]) -> None:
        """回滚当前批次已创建但尚未稳定返回给调用方的任务（必须持锁调用）。"""
        for task_id in task_ids:
            future = self._futures.pop(task_id, None)
            if future is not None:
                future.cancel()

            task = self._tasks.pop(task_id, None)
            if task:
                dedupe_key = _dedupe_task_key(task.stock_code, task.user_id)
                if self._analyzing_stocks.get(dedupe_key) == task_id:
                    del self._analyzing_stocks[dedupe_key]

    def get_task(self, task_id: str, user_id: Optional[int] = None) -> Optional[TaskInfo]:
        """
        获取任务信息（带 ToC 隔离）。

        Args:
            task_id: 任务 ID
            user_id: ToC 模式下做归属校验，跨用户时返回 None

        Returns:
            TaskInfo 或 None
        """
        with self._data_lock:
            task = self._tasks.get(task_id)
            if task and user_id is not None and task.user_id != user_id:
                return None
            return task.copy() if task else None

    def list_pending_tasks(self, user_id: Optional[int] = None) -> List[TaskInfo]:
        """获取所有进行中的任务（pending + processing），可选按用户过滤。"""
        with self._data_lock:
            return [
                task.copy() for task in self._tasks.values()
                if task.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)
                and (user_id is None or task.user_id == user_id)
            ]

    def list_all_tasks(self, limit: int = 50, user_id: Optional[int] = None) -> List[TaskInfo]:
        """
        获取所有任务（按创建时间倒序）。

        Args:
            limit: 返回数量限制
            user_id: ToC 模式下按用户过滤

        Returns:
            任务列表（拷贝）
        """
        with self._data_lock:
            tasks = sorted(
                [
                    task for task in self._tasks.values()
                    if user_id is None or task.user_id == user_id
                ],
                key=lambda t: t.created_at,
                reverse=True
            )
            return [t.copy() for t in tasks[:limit]]

    def get_task_stats(self, user_id: Optional[int] = None) -> Dict[str, int]:
        """返回按状态分组统计的任务计数（可选按用户过滤）。"""
        with self._data_lock:
            stats = {
                "total": 0,
                "pending": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0,
            }
            for task in self._tasks.values():
                if user_id is not None and task.user_id != user_id:
                    continue
                stats["total"] += 1
                stats[task.status.value] = stats.get(task.status.value, 0) + 1
            return stats

    def update_task_progress(
        self,
        task_id: str,
        progress: int,
        message: Optional[str] = None,
        *,
        event_type: str = "task_progress",
    ) -> Optional[TaskInfo]:
        """
        更新进行中任务的进度并广播 SSE 事件。

        仅 pending/processing 任务会被更新；进度被夹到 [0, 99]，保证终态由
        完成/失败路径单独控制。
        """
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task or task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                return None

            # 单调递增的进度，避免下游回退；99 是上限，100 由完成态独占
            next_progress = max(task.progress, max(0, min(99, int(progress))))
            changed = False
            if next_progress != task.progress:
                task.progress = next_progress
                changed = True
            if message is not None and message != task.message:
                task.message = message
                changed = True

            if not changed:
                return task.copy()

            task_snapshot = task.copy()

        self._broadcast_event(event_type, task_snapshot.to_dict(), user_id=task_snapshot.user_id)
        return task_snapshot
    
    # ========== 任务执行 ==========
    
    def _execute_task(
        self,
        task_id: str,
        stock_code: str,
        report_type: str,
        force_refresh: bool,
        notify: bool = True,
        skills: Optional[List[str]] = None,
        *,
        user_id: Optional[int] = None,
        refund_analysis_quota: bool = False,
        quota_refund_date: Optional[date] = None,
        refund_analysis_credits: bool = False,
        analysis_credit_cost: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        执行分析任务（在线程池中运行）。

        Args:
            task_id: 任务 ID
            stock_code: 股票代码
            report_type: 报告类型
            force_refresh: 是否强制刷新
            user_id: ToC 模式下的归属用户 ID

        Returns:
            分析结果字典；失败返回 None
        """
        # 更新状态为处理中
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now()
            task.message = "正在分析中..."
            task.progress = 10
            query_source = task.query_source or "api"
            analysis_phase = task.analysis_phase or "auto"
            portfolio_context = (
                dict(task.portfolio_context)
                if isinstance(task.portfolio_context, dict)
                else None
            )

        self._broadcast_event("task_started", task.to_dict(), user_id=task.user_id)

        try:
            # 延迟导入 AnalysisService 以避免循环依赖
            from src.services.analysis_service import AnalysisService

            service = AnalysisService()

            def _on_progress(progress: int, message: str) -> None:
                """把 pipeline 进度回调桥接到队列事件广播。"""
                self.update_task_progress(task_id, progress, message)

            result = service.analyze_stock(
                stock_code=stock_code,
                report_type=report_type,
                force_refresh=force_refresh,
                query_id=task_id,
                send_notification=notify,
                progress_callback=_on_progress,
                skills=skills,
                query_source=query_source,
                analysis_phase=analysis_phase,
                portfolio_context=portfolio_context,
                user_id=user_id,
            )

            if result:
                # 任务成功：标记完成、释放去重 key
                with self._data_lock:
                    task = self._tasks.get(task_id)
                    if task:
                        task.status = TaskStatus.COMPLETED
                        task.progress = 100
                        task.completed_at = datetime.now()
                        task.result = result
                        task.message = "分析完成"
                        task.stock_name = result.get("stock_name", task.stock_name)

                        # 从分析中集合移除
                        dedupe_key = _dedupe_task_key(task.stock_code, task.user_id)
                        if dedupe_key in self._analyzing_stocks:
                            del self._analyzing_stocks[dedupe_key]

                self._broadcast_event("task_completed", task.to_dict(), user_id=task.user_id)
                logger.info(f"[TaskQueue] 任务完成: {task_id} ({stock_code})")

                # 清理过期任务
                self._cleanup_old_tasks()

                return result
            else:
                # 分析返回空结果：转为失败处理
                raise Exception(service.last_error or "分析返回空结果")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[TaskQueue] 任务失败: {task_id} ({stock_code}), 错误: {error_msg}")

            with self._data_lock:
                task = self._tasks.get(task_id)
                if task:
                    task.status = TaskStatus.FAILED
                    task.completed_at = datetime.now()
                    task.error = error_msg[:200]  # 限制错误信息长度
                    task.message = f"分析失败: {error_msg[:50]}"

                    # 从分析中集合移除
                    dedupe_key = _dedupe_task_key(task.stock_code, task.user_id)
                    if dedupe_key in self._analyzing_stocks:
                        del self._analyzing_stocks[dedupe_key]

            if task:
                self._broadcast_event("task_failed", task.to_dict(), user_id=task.user_id)
                # 失败时按配置退还当日配额 / 积分（best-effort）
                if task.refund_analysis_quota:
                    self._refund_analysis_quota(task.user_id, task.quota_refund_date)
                if task.refund_analysis_credits:
                    self._refund_analysis_credits(task.user_id, task.task_id, task.analysis_credit_cost)

            # 清理过期任务
            self._cleanup_old_tasks()

            return None

    def _execute_background_task(
        self,
        task_id: str,
        run_task: Callable[[], Optional[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        """执行通用后台任务：用户自定义函数 + 标准任务生命周期。"""
        with self._data_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now()
            task.message = "任务执行中"
            task.progress = 10
            self._broadcast_event("task_started", task.to_dict(), user_id=task.user_id)

        try:
            result = run_task()
            if result is None:
                raise RuntimeError("任务返回空结果，未生成可持久化内容")

            with self._data_lock:
                task = self._tasks.get(task_id)
                if task:
                    task.status = TaskStatus.COMPLETED
                    task.progress = 100
                    task.completed_at = datetime.now()
                    task.result = result
                    task.message = "任务执行完成"

            self._broadcast_event("task_completed", task.to_dict(), user_id=task.user_id)
            logger.info(f"[TaskQueue] 自定义任务完成: {task_id}")

            self._cleanup_old_tasks()
            return result

        except Exception as e:  # pragma: no cover - behavior verified in downstream tests
            error_msg = str(e)
            logger.error(
                f"[TaskQueue] 自定义任务失败: {task_id}, 错误: {error_msg}"
            )

            with self._data_lock:
                task = self._tasks.get(task_id)
                if task:
                    task.status = TaskStatus.FAILED
                    task.completed_at = datetime.now()
                    task.error = error_msg[:200]
                    task.message = f"任务失败: {error_msg[:80]}"

            if task:
                self._broadcast_event("task_failed", task.to_dict(), user_id=task.user_id)

            self._cleanup_old_tasks()
            return None

    def _cleanup_old_tasks(self) -> int:
        """
        清理已完成的过期任务，只保留最近 ``_max_history`` 条。

        Returns:
            清理的任务数量
        """
        with self._data_lock:
            if len(self._tasks) <= self._max_history:
                return 0

            # 按时间排序，删除旧的已完成任务
            completed_tasks = sorted(
                [t for t in self._tasks.values()
                 if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)],
                key=lambda t: t.created_at
            )

            to_remove = len(self._tasks) - self._max_history
            removed = 0

            for task in completed_tasks[:to_remove]:
                del self._tasks[task.task_id]
                if task.task_id in self._futures:
                    del self._futures[task.task_id]
                removed += 1

            if removed > 0:
                logger.debug(f"[TaskQueue] 清理了 {removed} 个过期任务")

            return removed
    
    # ========== SSE 事件广播 ==========
    
    def _refund_analysis_quota(self, user_id: Optional[int], quota_date: Optional[date]) -> None:
        """付费分析失败时 best-effort 退还当日分析配额。"""
        if user_id is None:
            return
        try:
            from src.storage import DatabaseManager
            from src.users.quota import KIND_ANALYSIS, refund

            session = DatabaseManager.get_instance().get_session()
            try:
                refund(session, user_id=int(user_id), kind=KIND_ANALYSIS, on_date=quota_date)
                session.commit()
            finally:
                session.close()
        except Exception:
            logger.warning(
                "[TaskQueue] 任务失败后的分析配额返还失败: user_id=%s",
                user_id,
                exc_info=True,
            )

    def _refund_analysis_credits(
        self,
        user_id: Optional[int],
        task_id: Optional[str],
        analysis_credit_cost: int,
    ) -> None:
        """best-effort 退还积分：通过 idempotency_key 保证同 task 多次失败只退一次。"""
        if user_id is None:
            return
        try:
            from src.storage import AppUser, DatabaseManager
            from src.users.credits import KIND_ANALYSIS, refund_credits

            session = DatabaseManager.get_instance().get_session()
            try:
                amount = int(analysis_credit_cost or 0)
                if amount <= 0:
                    return
                user = session.query(AppUser).filter(AppUser.id == int(user_id)).first()
                if user is None:
                    return
                refund_credits(
                    session,
                    user=user,
                    amount=amount,
                    kind=KIND_ANALYSIS,
                    related_type="analysis",
                    related_id=task_id,
                    idempotency_key=f"analysis-failed-credit-refund:{task_id}",
                )
                session.commit()
            finally:
                session.close()
        except Exception:
            logger.warning(
                "[TaskQueue] 任务失败后的分析积分返还失败: user_id=%s task_id=%s",
                user_id,
                task_id,
                exc_info=True,
            )

    def subscribe(self, queue: 'AsyncQueue', user_id: Optional[int] = None) -> None:
        """
        订阅任务事件（SSE）。

        Args:
            queue: asyncio.Queue 实例，用于接收事件
            user_id: 可选的归属用户过滤；None 表示接收所有用户事件
        """
        with self._subscribers_lock:
            self._subscribers.append((queue, user_id))
            # 尝试捕获当前事件循环（应在主线程 async 上下文中调用）
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                # 不在 async 上下文时退回到 get_event_loop 兼容旧用法
                try:
                    self._main_loop = asyncio.get_event_loop()
                except RuntimeError:
                    pass
            logger.debug(f"[TaskQueue] 新订阅者加入，当前订阅者数: {len(self._subscribers)}")

    def unsubscribe(self, queue: 'AsyncQueue') -> None:
        """取消订阅任务事件。"""
        with self._subscribers_lock:
            before = len(self._subscribers)
            self._subscribers = [
                (subscriber_queue, subscriber_user_id)
                for subscriber_queue, subscriber_user_id in self._subscribers
                if subscriber_queue is not queue
            ]
            if len(self._subscribers) != before:
                logger.debug(f"[TaskQueue] 订阅者离开，当前订阅者数: {len(self._subscribers)}")

    def _broadcast_event(self, event_type: str, data: Dict[str, Any], user_id: Optional[int] = None) -> None:
        """
        广播事件到所有订阅者。

        使用 ``call_soon_threadsafe`` 保证跨线程安全：工作线程通过主事件循环
        把事件写入订阅者的 asyncio.Queue，订阅者在 async 上下文中读取。

        Args:
            event_type: 事件类型
            data: 事件数据
            user_id: 当前任务归属用户；None 时所有订阅者都能收到
        """
        event = {"type": event_type, "data": data}

        with self._subscribers_lock:
            subscribers = self._subscribers.copy()
            loop = self._main_loop

        if not subscribers:
            return

        if loop is None:
            logger.warning("[TaskQueue] 无法广播事件：主事件循环未设置")
            return

        for queue, subscriber_user_id in subscribers:
            # ToC 模式下按 user_id 过滤；None user_id 视为接收所有
            if subscriber_user_id is not None and subscriber_user_id != user_id:
                continue
            try:
                # call_soon_threadsafe 是从工作线程跨到主事件循环的安全通道
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError as e:
                # 事件循环已关闭时静默丢弃
                logger.debug(f"[TaskQueue] 广播事件跳过（循环已关闭）: {e}")
            except Exception as e:
                logger.warning(f"[TaskQueue] 广播事件失败: {e}")

    def shutdown(self) -> None:
        """关闭任务队列：等待已提交任务完成后释放线程池。"""
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None
            logger.info("[TaskQueue] 线程池已关闭")


# ========== 便捷函数 ==========

def get_task_queue() -> AnalysisTaskQueue:
    """获取任务队列单例，并按 config 同步最大并发数。"""
    queue = AnalysisTaskQueue()
    try:
        from src.config import get_config

        config = get_config()
        # 读取 MAX_WORKERS 配置；非法时保持当前并发
        target_workers = max(1, int(getattr(config, "max_workers", queue.max_workers)))
        queue.sync_max_workers(target_workers, log=False)
    except Exception as exc:
        logger.debug("[TaskQueue] 读取 MAX_WORKERS 失败，使用当前并发设置: %s", exc)

    return queue
