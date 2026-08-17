# -*- coding: utf-8 -*-
"""User-isolated AI decision-signal endpoints."""

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
    DecisionSignalStatusUpdateRequest,
    DecisionSignalSyncResponse,
)
from src.services.decision_signal_service import DecisionSignalNotFoundError, DecisionSignalService
from src.storage import AppUser, DatabaseManager

logger = logging.getLogger(__name__)
router = APIRouter()


def _service_error(message: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", message, exc, exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": message},
    )


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
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
