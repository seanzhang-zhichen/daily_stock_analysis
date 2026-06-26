# -*- coding: utf-8 -*-
"""Global API error handling helpers.

这里同时提供 Starlette middleware 和 FastAPI exception handlers。middleware
兜住调用链中未被 handler 捕获的异常；exception handlers 负责把常见异常转成
前端统一消费的 ``{"error", "message", "detail"}`` 响应结构。
"""

import logging
import traceback
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Catch unhandled request exceptions and return a normalized 500 body."""
    
    async def dispatch(
        self, 
        request: Request, 
        call_next: Callable
    ) -> Response:
        """Run the next handler and convert unexpected exceptions to JSON."""
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
    """Attach exception handlers that keep API error payloads consistent."""
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError
    
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Handle explicit HTTPException raised by endpoints/dependencies."""
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
        """Handle request validation errors from FastAPI/Pydantic."""
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
        """Handle any remaining exception not matched by a narrower handler."""
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
