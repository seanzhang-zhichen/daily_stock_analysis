# -*- coding: utf-8 -*-
"""全局 API 错误处理工具。

本模块同时提供 Starlette middleware 与 FastAPI exception handlers。

- ``ErrorHandlerMiddleware`` 作为兜底中间件，捕获调用链中未被任何 exception
  handler 处理的异常，并把响应统一转换为 ``{"error", "message", "detail"}`` 结构。
- ``add_error_handlers`` 注册多个细粒度的 exception handler，保证
  ``HTTPException``、``RequestValidationError`` 等常见异常返回一致的 JSON 结构。
"""

import logging
import traceback
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """兜底中间件：将请求链中未处理的异常转换为标准 500 JSON 响应。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable
    ) -> Response:
        """执行下游 handler 并把未预期异常序列化为统一 JSON 响应。"""
        try:
            response = await call_next(request)
            return response

        except Exception as e:
            # 记录完整上下文，避免生产环境响应体隐藏细节后日志也缺少定位信息。
            logger.error(
                f"未处理的异常: {e}\n"
                f"请求路径: {request.url.path}\n"
                f"请求方法: {request.method}\n"
                f"堆栈: {traceback.format_exc()}"
            )

            # 响应体保持稳定结构，detail 只在 DEBUG 日志级别下暴露异常文本。
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_error",
                    "message": "服务器内部错误，请稍后重试",
                    "detail": str(e) if logger.isEnabledFor(logging.DEBUG) else None
                }
            )


def add_error_handlers(app) -> None:
    """注册异常 handler，使 API 错误响应体结构保持一致。"""
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """处理 endpoint / 依赖中显式抛出的 ``HTTPException``。"""
        # endpoint 可直接传入标准错误 dict；此处保留原样，避免二次包装破坏字段。
        if isinstance(exc.detail, dict) and "error" in exc.detail and "message" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail
            )
        # 兼容 FastAPI 默认字符串 detail，转换为统一响应格式。
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "http_error",
                "message": str(exc.detail) if exc.detail else "HTTP Error",
                "detail": None
            }
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """处理 FastAPI/Pydantic 抛出的请求参数校验错误。"""
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": "请求参数验证失败",
                "detail": exc.errors()
            }
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """处理未被前面更具体的 handler 捕获的所有其它异常。"""
        logger.error(
            f"未处理的异常: {exc}\n"
            f"请求路径: {request.url.path}\n"
            f"堆栈: {traceback.format_exc()}"
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "服务器内部错误",
                "detail": None
            }
        )
