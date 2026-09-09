# -*- coding: utf-8 -*-
"""API v1 各路由共享的响应模型（common schemas）。

这些模型描述跨业务域复用的基础响应结构，例如根路由、健康检查、统一错误响应
和简单成功响应。具体业务响应应继续放在对应 schema 文件中，避免 common 变成
无边界的大杂烩。
"""

from typing import Optional, Any

from pydantic import BaseModel, Field


class RootResponse(BaseModel):
    """API-only 根路由返回的轻量服务状态。"""
    
    # 用于告诉调用方 API 当前在线，并附带版本号（可选）
    message: str = Field(..., description="API 运行状态消息", example="Daily Stock Analysis API is running")
    version: Optional[str] = Field(None, description="API 版本", example="1.0.0")
    
    class Config:
        """配置根状态响应在 OpenAPI 文档中的示例元数据。"""
        json_schema_extra = {
            "example": {
                "message": "Daily Stock Analysis API is running",
                "version": "1.0.0"
            }
        }


class HealthResponse(BaseModel):
    """健康检查响应，供负载均衡、监控和 smoke test 使用。"""
    
    # "ok" 表示正常；其他值（如 "degraded"）由具体端点自定义
    status: str = Field(..., description="服务状态", example="ok")
    # 服务端生成的时间戳，便于排查跨节点时钟漂移
    timestamp: Optional[str] = Field(None, description="时间戳")
    
    class Config:
        """配置健康检查响应在 OpenAPI 文档中的示例元数据。"""
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
    
    # 稳定的错误码，前端可以基于此做 i18n / 分支处理
    error: str = Field(..., description="错误类型", example="validation_error")
    # 人类可读的错误消息
    message: str = Field(..., description="错误详情", example="请求参数错误")
    # 可选结构化详情：例如验证错误列表、冲突字段等
    detail: Optional[Any] = Field(None, description="附加错误信息")
    
    class Config:
        """配置统一错误响应在 OpenAPI 文档中的示例元数据。"""
        json_schema_extra = {
            "example": {
                "error": "not_found",
                "message": "资源不存在",
                "detail": None
            }
        }


class SuccessResponse(BaseModel):
    """通用成功响应，适用于不需要专门业务模型的简单操作。"""
    
    # 固定为 True；如果需要失败态请改用 :class:`ErrorResponse`
    success: bool = Field(True, description="是否成功")
    # 可选的简短消息（如 "订阅成功"）
    message: Optional[str] = Field(None, description="成功消息")
    # 任意业务负载（前端通常直接使用 data.*）
    data: Optional[Any] = Field(None, description="响应数据")
    
    class Config:
        """配置通用成功响应在 OpenAPI 文档中的示例元数据。"""
        json_schema_extra = {
            "example": {
                "success": True,
                "message": "操作成功",
                "data": None
            }
        }
