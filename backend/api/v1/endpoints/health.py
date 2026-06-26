# -*- coding: utf-8 -*-
"""API v1 health-check endpoint.

该路由只返回进程级存活状态和当前时间戳，不访问数据库、外部行情源或 LLM 服务。
它适合作为负载均衡和本地 smoke test 的轻量探针；更深入的依赖检查应放到专门的
诊断接口中，避免健康检查自身拖慢主服务。
"""

from datetime import datetime

from fastapi import APIRouter

from api.v1.schemas.common import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return a lightweight liveness response for API v1."""
    return HealthResponse(
        status="ok",
        timestamp=datetime.now().isoformat()
    )
