# -*- coding: utf-8 -*-
"""Stock selection endpoints."""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_current_user, get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.stock_selection import (
    StockSelectionRequest,
    StockSelectionResponse,
    StockSelectionStrategiesResponse,
    StockSelectionStrategyItem,
)
from src.services.stock_selection import StockSelectionService
from src.storage import AppUser, DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/strategies",
    response_model=StockSelectionStrategiesResponse,
    responses={500: {"description": "Server error", "model": ErrorResponse}},
    summary="List stock selection strategies",
)
def list_stock_selection_strategies(
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> StockSelectionStrategiesResponse:
    """Return currently registered stock selection strategies."""
    _ = current_user
    try:
        service = StockSelectionService(db=db_manager)
        return StockSelectionStrategiesResponse(
            items=[StockSelectionStrategyItem(**asdict(item)) for item in service.list_strategies()]
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("List stock selection strategies failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"选股策略列表获取失败: {exc}"},
        )


@router.post(
    "/run",
    response_model=StockSelectionResponse,
    responses={
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Run stock selection",
    description="Run a registered stock selection strategy. Defaults to near-new-high selection.",
)
def run_stock_selection(
    request: StockSelectionRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> StockSelectionResponse:
    """Run stock selection for the current request."""
    _ = current_user
    try:
        service = StockSelectionService(db=db_manager)
        result = service.select(
            strategy_name=request.strategy,
            stock_codes=request.stock_codes,
            markets=request.markets,
            limit=request.limit,
            target_date=request.target_date,
            params={
                "lookback_days": request.lookback_days,
                "min_high_position": request.min_high_position,
                "recent_high_days": request.recent_high_days,
                "sort_by": request.sort_by,
            },
        )
        return StockSelectionResponse(**result.to_dict())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_params", "message": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Run stock selection failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"选股执行失败: {exc}"},
        )
