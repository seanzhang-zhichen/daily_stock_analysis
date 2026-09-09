# -*- coding: utf-8 -*-
"""选股（Stock Selection）HTTP 路由。

对外提供两类接口：列出当前已注册的选股策略元数据；根据请求执行一次选股
策略并返回候选结果。默认策略为"临近新高"（near_new_high），可通过请求体
的 ``strategy`` 字段切换。
"""

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
    """返回当前已注册的选股策略清单。"""
    _ = current_user
    try:
        service = StockSelectionService(db=db_manager)
        return StockSelectionStrategiesResponse(
            items=[StockSelectionStrategyItem(**asdict(item)) for item in service.list_strategies()]
        )
    except Exception as exc:  # noqa: BLE001
        # 抓取所有异常以避免策略元数据获取失败时连带整个模块崩溃
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
    """运行一次选股策略并返回候选结果。"""
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
                # 把 Pydantic 字段映射为服务层通用的 params dict，便于策略实现按需读取
                "lookback_days": request.lookback_days,
                "min_high_position": request.min_high_position,
                "recent_high_days": request.recent_high_days,
                "sort_by": request.sort_by,
            },
        )
        return StockSelectionResponse(**result.to_dict())
    except ValueError as exc:
        # 业务参数错误（如策略名非法、参数越界）以 400 返回
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_params", "message": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001
        # 兜底：策略执行失败时把异常原文透出，便于前端/排障定位
        logger.error("Run stock selection failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"选股执行失败: {exc}"},
        )
