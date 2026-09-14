# -*- coding: utf-8 -*-
"""选股（Screening）API 端点模块。

本模块对外暴露选股服务状态、策略清单、热点主题、选股任务提交/查询、
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

# FastAPI 路由实例，本模块所有端点均挂载于此
router = APIRouter()


def api_error(status_code: int, error: str, message: str) -> HTTPException:
    """构造统一格式的 HTTPException，使前端能按 error/message 解析业务错误。

    Args:
        status_code: HTTP 状态码
        error: 错误类型标识
        message: 错误描述

    Returns:
        HTTPException: 包含统一格式 detail 的异常对象
    """
    return HTTPException(status_code=status_code, detail={"error": error, "message": message})


class ScreeningScreenRequest(BaseModel):
    """同步/异步执行一次选股任务的请求载荷。

    包含市场、策略、最大结果数等参数，用于控制选股行为。
    """

    market: str = Field("cn", min_length=1, max_length=16)
    """目标市场标识，如 'cn' 表示 A 股，默认 'cn'"""

    strategy: str = Field("dual_low", min_length=1, max_length=64)
    """选股策略标识，如 'dual_low' 表示双低策略，默认 'dual_low'"""

    max_results: int = Field(20, ge=1, le=100)
    """最大返回结果数，范围 1-100，默认 20"""

    variant_seed: str = Field("", max_length=128)
    """变体种子，用于控制选股结果的随机性或变体选择，默认空字符串"""


class ScreeningStrategyResponse(BaseModel):
    """单个选股策略的可序列化元数据。

    包含策略的基本信息、适用市场、关联分析技能等，用于前端展示策略选择界面。
    """

    id: str
    """策略唯一标识"""

    name: str = ""
    """策略内部名称"""

    title: str = ""
    """策略展示标题"""

    description: str = ""
    """策略详细描述"""

    category: str = ""
    """策略分类，如价值型、成长型等"""

    tag: str = ""
    """策略标签"""

    tags: List[str] = Field(default_factory=list)
    """策略标签列表"""

    market_scope: List[str] = Field(default_factory=list)
    """策略适用的市场范围"""

    market: str = ""
    """策略默认市场"""

    analysis_skills: List[str] = Field(default_factory=list)
    """策略关联的分析技能列表"""


class ScreeningScreenAccepted(BaseModel):
    """异步选股任务被接受后的即时响应（HTTP 202）。

    包含任务追踪信息，前端可据此轮询任务状态。
    """

    task_id: str
    """任务唯一标识"""

    trace_id: str
    """追踪 ID，用于链路追踪，通常与 task_id 相同"""

    status: str = "pending"
    """任务初始状态，默认 'pending'"""

    message: str
    """任务提交成功提示信息"""

    strategy: str
    """本次选股使用的策略"""

    market: str
    """本次选股的目标市场"""

    max_results: int
    """本次选股的最大结果数"""


class ScreeningScreenTaskStatus(BaseModel):
    """异步选股任务的状态轮询响应。

    包含任务当前进度、状态、错误信息及最终结果。
    """

    task_id: str
    """任务唯一标识"""

    trace_id: Optional[str] = None
    """追踪 ID"""

    status: str
    """当前任务状态，如 pending/processing/completed/failed"""

    progress: int = 0
    """任务进度百分比，0-100"""

    message: Optional[str] = None
    """当前阶段描述信息"""

    error: Optional[str] = None
    """错误信息，任务失败时非空"""

    result: Optional[Dict[str, Any]] = None
    """选股结果，任务完成时非空"""


def _service(config: Config, db_manager: Any = None, user_id: Optional[int] = None) -> ScreeningService:
    """构造 ScreeningService 实例。

    当 db_manager 缺少写接口时降级为 None，避免服务层崩溃。
    这种防御式降级防止了旧版存储驱动或只读副本被误传入时引发 AttributeError。

    Args:
        config: 应用配置
        db_manager: 数据库管理器，可选
        user_id: 用户 ID，可选

    Returns:
        ScreeningService: 选股服务实例
    """
    # 仅在 db_manager 实现了 save_screening_run 等写接口时才注入，否则视作不可用。
    # 这种防御式降级防止了旧版存储驱动或只读副本被误传入时引发 AttributeError。
    usable_db = db_manager if callable(getattr(db_manager, "save_screening_run", None)) else None
    return ScreeningService(config=config, db_manager=usable_db, user_id=user_id)


def _screening_task_not_found(task_id: str) -> HTTPException:
    """构造统一的"选股任务不存在或已过期"错误响应。

    Args:
        task_id: 任务 ID

    Returns:
        HTTPException: 404 错误，包含任务不存在信息
    """
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
    """返回选股服务的整体运行状态（配置可用性、依赖健康度等）。

    Args:
        config: 应用配置
        current_user: 当前登录用户

    Returns:
        Dict[str, Any]: 服务状态信息字典
    """
    _ = current_user
    return _service(config).status()


@router.get("/strategies")
def screening_strategies(
    request: Request,
    config: Config = Depends(get_config_dep),
    current_user: AppUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """返回当前可选的选股策略清单及其元数据。

    Args:
        request: HTTP 请求对象
        config: 应用配置
        current_user: 当前登录用户

    Returns:
        Dict[str, Any]: 策略列表及元数据
    """
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
    """拉取近期热点主题列表，可选强制刷新与是否包含详情。

    Args:
        provider: 数据提供商标识，可选
        top: 返回热点数量，范围 1-50，默认 12
        refresh: 是否强制刷新缓存，默认 False
        include_details: 是否包含详细描述，默认 False
        config: 应用配置
        current_user: 当前登录用户

    Returns:
        Dict[str, Any]: 热点主题列表
    """
    _ = current_user
    # 兼容 FastAPI 在某些情况下把 Query 对象传进来的边缘场景，确保得到原生 bool。
    # 例如测试框架或某些代理可能会注入 Query 对象而非直接值。
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
    """获取某个热点主题的详情（含关联搜索结果）。

    Args:
        topic: 热点主题标识，路径参数
        provider: 数据提供商标识，可选
        refresh: 是否强制刷新缓存，默认 False
        include_search: 是否包含关联搜索结果，默认 False
        config: 应用配置
        current_user: 当前登录用户

    Returns:
        Dict[str, Any]: 热点主题详情
    """
    _ = current_user
    # 兼容 FastAPI 在某些情况下把 Query 对象传进来的边缘场景，确保得到原生 bool。
    # 例如测试框架或某些代理可能会注入 Query 对象而非直接值。
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
    """提交一次异步选股任务，立即返回 task_id，调用方按 task_id 轮询结果。

    选股任务在后台执行，前端通过 task_id 轮询进度和结果。
    任务执行过程中会通过进度回调更新任务状态。

    Args:
        request: 选股请求，包含市场、策略、最大结果数等
        http_request: HTTP 请求对象
        config: 应用配置
        db_manager: 数据库管理器
        current_user: 当前登录用户

    Returns:
        ScreeningScreenAccepted: 任务已接受响应，包含 task_id 和 trace_id
    """
    task_id = uuid.uuid4().hex
    task_queue = get_task_queue()

    def run_screen() -> Dict[str, Any]:
        """在后台执行整轮选股并随阶段回调进度，最终返回候选结果。

        选股可能耗时较长（外部数据源、多策略打分），先把进度推到 20%，
        避免前端在任务启动后长时间看不到进度更新而误以为任务卡住。
        """
        task_queue.update_task_progress(
            task_id,
            20,
            "正在执行选股，外部数据源较慢时会持续后台运行",
        )

        def report_progress(progress: int, message: str) -> None:
            """把服务层回调的阶段进度转发到任务队列。

            服务层在抓取/打分各阶段回调，将进度反馈到任务队列，
            使前端轮询时能实时看到当前阶段。
            """
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
            # 98% 是"整理结果"阶段，留 2% 给任务队列自身标记完成，
            # 避免前端看到 100% 但任务状态仍是 processing。
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
    # trace_id 优先使用任务对象上的 trace_id，若不存在则回退到 task_id。
    # 这样即使底层队列未实现 trace_id，也能保证前端有唯一追踪标识。
    return ScreeningScreenAccepted(
        task_id=task.task_id,
        # trace_id 优先使用任务对象上的 trace_id，若不存在则回退到 task_id。
        # 这样即使底层队列未实现 trace_id，也能保证前端有唯一追踪标识。
        trace_id=getattr(task, "trace_id", None) or task.task_id,
        # status 字段兼容两种来源：QueueTaskStatus 枚举或纯字符串。
        # 使用 isinstance 做防御，避免不同队列实现导致 AttributeError。
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
    """轮询一次异步选股任务的进度、状态与最终结果。

    Args:
        task_id: 任务 ID，路径参数
        current_user: 当前登录用户

    Returns:
        ScreeningScreenTaskStatus: 任务当前状态、进度及结果

    Raises:
        HTTPException: 404 当任务不存在或已过期时
    """
    task = get_task_queue().get_task(task_id, user_id=current_user.id)
    # 仅允许查询属于本用户、且类型为选股的 task；
    # 其它情况统一按"不存在"返回，避免泄露他人任务存在性信息。
    if task is None or task.report_type != "screening_screen":
        raise _screening_task_not_found(task_id)

    result = task.result if task.status == QueueTaskStatus.COMPLETED and isinstance(task.result, dict) else None
    # trace_id 优先使用任务对象上的 trace_id，若不存在则回退到 task_id。
    # 这样即使底层队列未实现 trace_id，也能保证前端有唯一追踪标识。
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
    """同步执行一次选股并立即返回结果（适合短时调试或小规模数据）。

    注意：同步选股可能阻塞请求，建议生产环境使用异步接口 /screen/tasks。

    Args:
        request: 选股请求
        http_request: HTTP 请求对象
        config: 应用配置
        db_manager: 数据库管理器
        current_user: 当前登录用户

    Returns:
        Dict[str, Any]: 选股结果
    """
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
    """查询当前用户近期的选股运行历史，可按策略/市场过滤。

    Args:
        limit: 返回记录数，范围 1-100，默认 20
        strategy: 策略筛选，可选
        market: 市场筛选，可选
        config: 应用配置
        db_manager: 数据库管理器
        current_user: 当前登录用户

    Returns:
        Dict[str, Any]: 选股历史记录列表
    """
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
    """获取单次选股运行（run_id）的详情与候选结果。

    Args:
        run_id: 选股运行 ID，路径参数
        config: 应用配置
        db_manager: 数据库管理器
        current_user: 当前登录用户

    Returns:
        Dict[str, Any]: 选股运行详情及候选结果
    """
    _ = current_user
    return _service(config, db_manager, current_user.id).history_detail(run_id)


@router.get("/source-history")
def screening_source_history(
    limit: int = Query(100, ge=1, le=100),
    config: Config = Depends(get_config_dep),
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """查询选股过程中各数据源抓取历史的统计结果。

    Args:
        limit: 返回记录数，范围 1-100，默认 100
        config: 应用配置
        db_manager: 数据库管理器
        current_user: 当前登录用户

    Returns:
        Dict[str, Any]: 数据源抓取历史统计
    """
    return _service(config, db_manager, current_user.id).source_history(limit=limit)
