# -*- coding: utf-8 -*-
"""选股（Screening）相关的 HTTP 路由。

该模块对外暴露选股服务状态、策略清单、热点主题、选股任务提交/查询、
选股历史与抓取源历史等接口。任务型操作通过 `get_task_queue()` 走后台队列，
同步接口则直接调用 `ScreeningService`。所有路由统一挂载在调用方 router 之下。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_config_dep, get_database_manager, get_current_user
from src.config import Config
from src.services.screening_service import ScreeningService
from src.services.task_queue import TaskStatus as QueueTaskStatus
from src.services.task_queue import get_task_queue
from src.storage import AppUser, DatabaseManager

router = APIRouter()


def api_error(status_code: int, error: str, message: str) -> HTTPException:
    """构造统一格式的 HTTPException，使前端能按 error/message 解析业务错误。"""
    return HTTPException(status_code=status_code, detail={"error": error, "message": message})


class ScreeningScreenRequest(BaseModel):
    """同步/异步执行一次选股任务的请求载荷。"""

    market: str = Field("cn", min_length=1, max_length=16)
    strategy: str = Field("dual_low", min_length=1, max_length=64)
    max_results: int = Field(20, ge=1, le=100)
    variant_seed: str = Field("", max_length=128)


class ScreeningStrategyResponse(BaseModel):
    """单个选股策略的可序列化元数据。"""

    id: str
    name: str = ""
    title: str = ""
    description: str = ""
    category: str = ""
    tag: str = ""
    tags: List[str] = Field(default_factory=list)
    market_scope: List[str] = Field(default_factory=list)
    market: str = ""
    analysis_skills: List[str] = Field(default_factory=list)


class ScreeningScreenAccepted(BaseModel):
    """异步选股任务被接受后的即时响应（HTTP 202）。"""

    task_id: str
    trace_id: str
    status: str = "pending"
    message: str
    strategy: str
    market: str
    max_results: int


class ScreeningScreenTaskStatus(BaseModel):
    """异步选股任务的状态轮询响应。"""

    task_id: str
    trace_id: Optional[str] = None
    status: str
    progress: int = 0
    message: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


def _service(config: Config, db_manager: Any = None, user_id: Optional[int] = None) -> ScreeningService:
    """构造 ScreeningService：当 db_manager 缺少写接口时降级为 None，避免服务层崩溃。"""
    # 仅在 db_manager 实现了 save_screening_run 等写接口时才注入，否则视作不可用
    usable_db = db_manager if callable(getattr(db_manager, "save_screening_run", None)) else None
    return ScreeningService(config=config, db_manager=usable_db, user_id=user_id)


def _screening_task_not_found(task_id: str) -> HTTPException:
    """构造统一的"选股任务不存在或已过期"错误响应。"""
    return api_error(
        404,
        "screening_screen_task_not_found",
        f"选股任务 {task_id} 不存在或已过期",
    )


@router.get("/status")
def screening_status(
    config: Config = Depends(get_config_dep),
    current_user: AppUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """返回选股服务的整体运行状态（配置可用性、依赖健康度等）。"""
    _ = current_user
    return _service(config).status()


@router.get("/strategies")
def screening_strategies(
    request: Request,
    config: Config = Depends(get_config_dep),
    current_user: AppUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """返回当前可选的选股策略清单及其元数据。"""
    _ = current_user
    return _service(config).strategies()


@router.get("/hotspots")
def screening_hotspots(
    provider: str = Query("", max_length=32),
    top: int = Query(12, ge=1, le=50),
    refresh: bool = Query(False),
    include_details: bool = Query(False),
    config: Config = Depends(get_config_dep),
    current_user: AppUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """拉取近期热点主题列表，可选强制刷新与是否包含详情。"""
    _ = current_user
    # 兼容 FastAPI 在某些情况下把 Query 对象传进来的边缘场景，确保得到原生 bool
    refresh_value = refresh if isinstance(refresh, bool) else bool(getattr(refresh, "default", False))
    include_details_value = (
        include_details
        if isinstance(include_details, bool)
        else bool(getattr(include_details, "default", False))
    )
    return _service(config).hotspots(
        provider=provider,
        top=top,
        refresh=refresh_value,
        include_details=include_details_value,
    )


@router.get("/hotspots/{topic:path}")
def screening_hotspot_detail(
    topic: str,
    provider: str = Query("", max_length=32),
    refresh: bool = Query(False),
    include_search: bool = Query(False),
    config: Config = Depends(get_config_dep),
    current_user: AppUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """获取某个热点主题的详情（含关联搜索结果）。"""
    _ = current_user
    refresh_value = refresh if isinstance(refresh, bool) else bool(getattr(refresh, "default", False))
    include_search_value = (
        include_search
        if isinstance(include_search, bool)
        else bool(getattr(include_search, "default", False))
    )
    return _service(config).hotspot_detail(
        topic=topic,
        provider=provider,
        refresh=refresh_value,
        include_search=include_search_value,
    )


@router.post("/screen/tasks", status_code=202, response_model=ScreeningScreenAccepted)
def screening_start_screen_task(
    request: ScreeningScreenRequest,
    http_request: Request,
    config: Config = Depends(get_config_dep),
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> ScreeningScreenAccepted:
    """提交一次异步选股任务，立即返回 task_id，调用方按 task_id 轮询结果。"""
    task_id = uuid.uuid4().hex
    task_queue = get_task_queue()

    def run_screen() -> Dict[str, Any]:
        """在后台执行整轮选股并随阶段回调进度，最终返回候选结果。"""
        # 选股可能耗时较长，先把进度推到 20%，避免前端误以为任务卡住
        task_queue.update_task_progress(
            task_id,
            20,
            "正在执行选股，外部数据源较慢时会持续后台运行",
        )

        def report_progress(progress: int, message: str) -> None:
            """把服务层回调的阶段进度转发到任务队列。"""
            # 服务层在抓取/打分各阶段回调，将进度反馈到任务队列
            task_queue.update_task_progress(task_id, progress, message)

        result = _service(config, db_manager, current_user.id).screen(
            strategy=request.strategy,
            market=request.market,
            max_results=request.max_results,
            selection_seed=request.variant_seed,
            progress_callback=report_progress,
        )
        task_queue.update_task_progress(
            task_id,
            98,
            f"选股已完成，正在整理 {result.get('candidate_count', 0)} 条候选",
        )
        return result

    task = task_queue.submit_background_task(
        run_screen,
        stock_code="screening_screen",
        stock_name=f"{request.strategy} / {request.market}",
        report_type="screening_screen",
        message="选股任务已提交",
        task_id=task_id,
        user_id=current_user.id,
    )
    return ScreeningScreenAccepted(
        task_id=task.task_id,
        trace_id=getattr(task, "trace_id", None) or task.task_id,
        status=task.status.value if isinstance(task.status, QueueTaskStatus) else str(task.status),
        message=task.message or "选股任务已提交",
        strategy=request.strategy,
        market=request.market,
        max_results=request.max_results,
    )


@router.get("/screen/tasks/{task_id}", response_model=ScreeningScreenTaskStatus)
def screening_screen_task_status(
    task_id: str,
    current_user: AppUser = Depends(get_current_user),
) -> ScreeningScreenTaskStatus:
    """轮询一次异步选股任务的进度、状态与最终结果。"""
    task = get_task_queue().get_task(task_id, user_id=current_user.id)
    # 仅允许查询属于本用户、且类型为选股的 task；其它情况统一按"不存在"返回
    if task is None or task.report_type != "screening_screen":
        raise _screening_task_not_found(task_id)

    result = task.result if task.status == QueueTaskStatus.COMPLETED and isinstance(task.result, dict) else None
    return ScreeningScreenTaskStatus(
        task_id=task.task_id,
        trace_id=getattr(task, "trace_id", None) or task.task_id,
        status=task.status.value if isinstance(task.status, QueueTaskStatus) else str(task.status),
        progress=task.progress,
        message=task.message,
        error=task.error,
        result=result,
    )


@router.post("/screen")
def screening_screen(
    request: ScreeningScreenRequest,
    http_request: Request,
    config: Config = Depends(get_config_dep),
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """同步执行一次选股并立即返回结果（适合短时调试或小规模数据）。"""
    _ = current_user
    return _service(config, db_manager, current_user.id).screen(
        strategy=request.strategy,
        market=request.market,
        max_results=request.max_results,
        selection_seed=request.variant_seed,
    )


@router.get("/history")
def screening_history(
    limit: int = Query(20, ge=1, le=100),
    strategy: str = Query("", max_length=64),
    market: str = Query("", max_length=16),
    config: Config = Depends(get_config_dep),
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """查询当前用户近期的选股运行历史，可按策略/市场过滤。"""
    _ = current_user
    return _service(config, db_manager, current_user.id).history(
        limit=limit,
        strategy=strategy,
        market=market,
    )


@router.get("/history/{run_id}")
def screening_history_detail(
    run_id: str,
    config: Config = Depends(get_config_dep),
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """获取单次选股运行（run_id）的详情与候选结果。"""
    _ = current_user
    return _service(config, db_manager, current_user.id).history_detail(run_id)


@router.get("/source-history")
def screening_source_history(
    limit: int = Query(100, ge=1, le=100),
    config: Config = Depends(get_config_dep),
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """查询选股过程中各数据源抓取历史的统计结果。"""
    return _service(config, db_manager, current_user.id).source_history(limit=limit)
