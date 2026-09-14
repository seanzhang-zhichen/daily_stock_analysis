# -*- coding: utf-8 -*-
"""数据能力（Data Capability）API 端点模块。

本模块提供数据能力查询接口，包括公开的数据能力概览（当前用户可见）
和管理员级别的详细概览（包含提供方详情）。用于前端展示各数据源的可用状态。
"""

from fastapi import APIRouter, Depends

from api.deps import get_admin_user, get_current_user
from api.v1.schemas.data_capability import DataCapabilityResponse
from src.services.data_capability_service import DataCapabilityService
from src.storage import AppUser

# FastAPI 路由实例，本模块所有端点均挂载于此
router = APIRouter()


@router.get("/capabilities", response_model=DataCapabilityResponse)
def data_capabilities(_user: AppUser = Depends(get_current_user)) -> DataCapabilityResponse:
    """返回当前用户可见的数据能力概览。

    不包含提供方内部详情，用于普通用户查看数据源可用状态。

    Args:
        _user: 当前登录用户，由依赖注入提供

    Returns:
        DataCapabilityResponse: 数据能力概览响应
    """
    return DataCapabilityResponse(**DataCapabilityService().get_overview(include_provider_details=False))


@router.get("/overview", response_model=DataCapabilityResponse)
def data_overview(_admin: AppUser = Depends(get_admin_user)) -> DataCapabilityResponse:
    """返回管理员级别的数据能力详细概览。

    包含提供方内部详情，仅限管理员访问。

    Args:
        _admin: 当前管理员用户，由依赖注入提供

    Returns:
        DataCapabilityResponse: 数据能力详细概览响应
    """
    return DataCapabilityResponse(**DataCapabilityService().get_overview(include_provider_details=True))
