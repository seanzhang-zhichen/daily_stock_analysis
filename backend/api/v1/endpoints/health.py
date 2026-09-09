# -*- coding: utf-8 -*-
"""API v1 健康检查端点。

该路由只返回进程级存活状态和当前时间戳，不访问数据库、外部行情源或 LLM 服务。
它适合作为负载均衡和本地 smoke test 的轻量探针；更深入的依赖检查应放到专门的
诊断接口中，避免健康检查自身拖慢主服务。
"""

from datetime import datetime

from fastapi import APIRouter

from api.v1.schemas.common import HealthResponse

# 该 router 会被 ``api.v1.router`` 统一 include，挂到 ``/api/v1`` 前缀下
router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """返回进程级存活响应（status + 当前 ISO 时间戳）。

    返回示例::

        {"status": "ok", "timestamp": "2024-01-01T12:00:00.000000"}

    注意：本端点刻意不做依赖探测，确保它在 DB / 行情源异常时仍能快速返回。
    """
    return HealthResponse(
        status="ok",
        timestamp=datetime.now().isoformat()
    )
