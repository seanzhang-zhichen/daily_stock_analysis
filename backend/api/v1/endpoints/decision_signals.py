# -*- coding: utf-8 -*-
"""按用户隔离的 AI 决策信号 HTTP 路由。

提供 AI 决策信号的查询、详情、最新有效信号、按信号同步、出效评估运行/查询
以及状态更新。所有读写操作都通过 `user_id` 进行租户隔离，避免越权。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_current_user, get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.decision_signals import (
    DecisionSignalItem,
    DecisionSignalLatestResponse,
    DecisionSignalListResponse,
    DecisionSignalMutationResponse,
    DecisionSignalOutcomeListResponse,
    DecisionSignalOutcomeRunRequest,
    DecisionSignalOutcomeRunResponse,
    DecisionSignalStatusUpdateRequest,
    DecisionSignalSyncResponse,
    DecisionSignalFeedbackRequest,
    DecisionSignalFeedbackItem,
    DecisionSignalOutcomeStatsResponse,
    DecisionSignalReassessRequest,
    DecisionSignalReassessResponse,
)
from src.services.decision_signal_service import DecisionSignalNotFoundError, DecisionSignalService
from src.services.decision_signal_outcome_service import DecisionSignalOutcomeService
from src.services.decision_signal_feedback_service import DecisionSignalFeedbackService
from src.services.decision_signal_reassess_service import DecisionSignalReassessService
from src.storage import AppUser, DatabaseManager

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/reassess", response_model=DecisionSignalReassessResponse)
def reassess_decision_signal(request: DecisionSignalReassessRequest, current_user: AppUser = Depends(get_current_user), db_manager: DatabaseManager = Depends(get_database_manager)):
    try:
        result = DecisionSignalReassessService(db_manager).reassess(source_report_id=request.source_report_id, user_id=getattr(current_user, "id", None), persist=request.persist)
        return DecisionSignalReassessResponse(**result)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)}) from exc

@router.get("/outcomes/stats", response_model=DecisionSignalOutcomeStatsResponse)
def get_decision_signal_outcome_stats(horizon: Optional[str] = Query(None), current_user: AppUser = Depends(get_current_user), db_manager: DatabaseManager = Depends(get_database_manager)):
    try:
        return DecisionSignalOutcomeStatsResponse(**DecisionSignalOutcomeService(db_manager).stats(user_id=getattr(current_user, "id", None), horizon=horizon))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)}) from exc

@router.get("/{signal_id}/feedback", response_model=DecisionSignalFeedbackItem)
def get_signal_feedback(signal_id: int, current_user: AppUser = Depends(get_current_user), db_manager: DatabaseManager = Depends(get_database_manager)):
    item = DecisionSignalFeedbackService(db_manager).get(signal_id, user_id=getattr(current_user, "id", None))
    if item is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "decision signal not found"})
    return DecisionSignalFeedbackItem(**item)

@router.put("/{signal_id}/feedback", response_model=DecisionSignalFeedbackItem)
def put_signal_feedback(signal_id: int, request: DecisionSignalFeedbackRequest, current_user: AppUser = Depends(get_current_user), db_manager: DatabaseManager = Depends(get_database_manager)):
    item = DecisionSignalFeedbackService(db_manager).put(signal_id, user_id=getattr(current_user, "id", None), feedback_value=request.feedback_value, reason_code=request.reason_code, note=request.note, source=request.source)
    if item is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "decision signal not found"})
    return DecisionSignalFeedbackItem(**item)


@router.post("/{signal_id}/outcomes/run", response_model=DecisionSignalOutcomeRunResponse)
def run_decision_signal_outcomes(
    signal_id: int,
    request: DecisionSignalOutcomeRunRequest,
    current_user: AppUser = Depends(get_current_user),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> DecisionSignalOutcomeRunResponse:
    """对单条信号触发"出效评估"任务（多 horizon 评估）。"""
    try:
        result = DecisionSignalOutcomeService(db_manager).run(
            signal_id=signal_id, user_id=getattr(current_user, "id", None),
            horizons=request.horizons, force=request.force,
        )
        return DecisionSignalOutcomeRunResponse(**result)
    except DecisionSignalNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)}) from exc


@router.get("/{signal_id}/outcomes", response_model=DecisionSignalOutcomeListResponse)
def list_decision_signal_outcomes(
    signal_id: int,
    horizon: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AppUser = Depends(get_current_user),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> DecisionSignalOutcomeListResponse:
    """分页查询某条信号在各 horizon 下的出效评估记录。"""
    try:
        result = DecisionSignalOutcomeService(db_manager).list(
            signal_id=signal_id, user_id=getattr(current_user, "id", None),
            horizon=horizon, page=page, page_size=page_size,
        )
        return DecisionSignalOutcomeListResponse(**result)
    except DecisionSignalNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)}) from exc


def _service_error(message: str, exc: Exception) -> HTTPException:
    """将未预期的服务异常统一包装为内部错误响应，同时打印完整堆栈便于排障。"""
    logger.error("%s: %s", message, exc, exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": message},
    )


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """解析前端传入的 ISO 8601 时间串，失败时返回 400。"""
    if not value:
        return None
    try:
        # 兼容带 Z 后缀的 UTC 串；解析后去掉 tzinfo 便于与服务层朴素 datetime 比较
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": "时间格式必须为 ISO 8601"},
        ) from exc


@router.get(
    "",
    response_model=DecisionSignalListResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="查询当前用户的 AI 建议",
)
def list_decision_signals(
    market: Optional[str] = Query(None),
    stock_code: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    status: Optional[str] = Query("active"),
    created_from: Optional[str] = Query(None),
    created_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AppUser = Depends(get_current_user),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> DecisionSignalListResponse:
    """分页查询当前用户的 AI 决策信号，支持多维过滤。"""
    service = DecisionSignalService(db_manager)
    try:
        return DecisionSignalListResponse(**service.list_signals(
            user_id=getattr(current_user, "id", None),
            market=market,
            stock_code=stock_code,
            action=action,
            status=status,
            created_from=_parse_datetime(created_from),
            created_to=_parse_datetime(created_to),
            page=page,
            page_size=page_size,
        ))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise _service_error("查询 AI 建议失败", exc)


@router.post(
    "/sync",
    response_model=DecisionSignalSyncResponse,
    responses={500: {"model": ErrorResponse}},
    summary="从分析历史同步 AI 建议",
)
def sync_decision_signals(
    current_user: AppUser = Depends(get_current_user),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> DecisionSignalSyncResponse:
    """从用户的历史分析记录回填生成 AI 决策信号。"""
    try:
        created = DecisionSignalService(db_manager).sync_analysis_history(
            user_id=getattr(current_user, "id", None),
        )
        return DecisionSignalSyncResponse(created=created)
    except Exception as exc:
        raise _service_error("同步 AI 建议失败", exc)


@router.get(
    "/latest/{stock_code}",
    response_model=DecisionSignalLatestResponse,
    responses={500: {"model": ErrorResponse}},
    summary="查询单只股票的最新有效 AI 建议",
)
def get_latest_decision_signals(
    stock_code: str,
    current_user: AppUser = Depends(get_current_user),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> DecisionSignalLatestResponse:
    """获取指定股票当前仍处于有效状态的 AI 决策信号列表。"""
    try:
        items = DecisionSignalService(db_manager).latest(
            stock_code,
            user_id=getattr(current_user, "id", None),
        )
        return DecisionSignalLatestResponse(items=[DecisionSignalItem(**item) for item in items])
    except Exception as exc:
        raise _service_error("查询最新 AI 建议失败", exc)


@router.get(
    "/{signal_id}",
    response_model=DecisionSignalItem,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="查询 AI 建议详情",
)
def get_decision_signal(
    signal_id: int,
    current_user: AppUser = Depends(get_current_user),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> DecisionSignalItem:
    """根据 signal_id 查询单条 AI 决策信号的完整详情。"""
    try:
        item = DecisionSignalService(db_manager).get_signal(
            signal_id,
            user_id=getattr(current_user, "id", None),
        )
        return DecisionSignalItem(**item)
    except DecisionSignalNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise _service_error("查询 AI 建议详情失败", exc)


@router.patch(
    "/{signal_id}/status",
    response_model=DecisionSignalMutationResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="更新 AI 建议状态",
)
def update_decision_signal_status(
    signal_id: int,
    request: DecisionSignalStatusUpdateRequest,
    current_user: AppUser = Depends(get_current_user),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> DecisionSignalMutationResponse:
    """更新某条 AI 决策信号的状态（如 active/closed/archived）。"""
    try:
        item = DecisionSignalService(db_manager).update_status(
            signal_id,
            user_id=getattr(current_user, "id", None),
            status=request.status,
        )
        return DecisionSignalMutationResponse(item=DecisionSignalItem(**item))
    except DecisionSignalNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise _service_error("更新 AI 建议状态失败", exc)
