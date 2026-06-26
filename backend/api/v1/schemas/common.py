# -*- coding: utf-8 -*-
"""Shared response models used across API v1 endpoints.

这些模型描述跨业务域复用的基础响应结构，例如根路由、健康检查、统一错误响应
和简单成功响应。具体业务响应应继续放在对应 schema 文件中，避免 common 变成
无边界的大杂烩。
"""

from typing import Optional, Any

from pydantic import BaseModel, Field


class RootResponse(BaseModel):
    """API-only 根路由返回的轻量服务状态。"""
    
    message: str = Field(..., description="API 运行状态消息", example="Daily Stock Analysis API is running")
    version: Optional[str] = Field(None, description="API 版本", example="1.0.0")
    
    class Config:
        """Document OpenAPI example metadata for root status responses."""
        json_schema_extra = {
            "example": {
                "message": "Daily Stock Analysis API is running",
                "version": "1.0.0"
            }
        }


class HealthResponse(BaseModel):
    """健康检查响应，供负载均衡、监控和 smoke test 使用。"""
    
    status: str = Field(..., description="服务状态", example="ok")
    timestamp: Optional[str] = Field(None, description="时间戳")
    
    class Config:
        """Document OpenAPI example metadata for health check responses."""
        json_schema_extra = {
            "example": {
                "status": "ok",
                "timestamp": "2024-01-01T12:00:00"
            }
        }


class ErrorResponse(BaseModel):
    """统一错误响应结构。

    ``error`` 是稳定的机器可读错误码，``message`` 面向用户或前端展示，
    ``detail`` 用于放验证错误列表、冲突字段等可选调试信息。
    """
    
    error: str = Field(..., description="错误类型", example="validation_error")
    message: str = Field(..., description="错误详情", example="请求参数错误")
    detail: Optional[Any] = Field(None, description="附加错误信息")
    
    class Config:
        """Document OpenAPI example metadata for error responses."""
        json_schema_extra = {
            "example": {
                "error": "not_found",
                "message": "资源不存在",
                "detail": None
            }
        }


class SuccessResponse(BaseModel):
    """通用成功响应，适用于不需要专门业务模型的简单操作。"""
    
    success: bool = Field(True, description="是否成功")
    message: Optional[str] = Field(None, description="成功消息")
    data: Optional[Any] = Field(None, description="响应数据")
    
    class Config:
        """Document OpenAPI example metadata for generic success responses."""
        json_schema_extra = {
            "example": {
                "success": True,
                "message": "操作成功",
                "data": None
            }
        }
