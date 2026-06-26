# -*- coding: utf-8 -*-
"""Analysis, task queue, and market-review API schemas.

这些模型覆盖单股/批量分析请求、异步任务受理响应、任务状态查询，以及大盘复盘
后台任务。字段命名需要和前端任务轮询、SSE 事件以及历史记录服务保持兼容。
"""

from typing import Optional, List, Any
from enum import Enum

from pydantic import BaseModel, Field
from src.utils.analysis_metadata import SELECTION_SOURCE_PATTERN


class TaskStatusEnum(str, Enum):
    """Task lifecycle states exposed to API clients and SSE subscribers."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalyzeRequest(BaseModel):
    """Request body for triggering stock analysis.

    ``stock_code`` 用于单只股票，``stock_codes`` 用于批量分析，两者由 endpoint
    层做互斥/必填校验。``stock_name``、``original_query``、``selection_source``
    保留用户输入上下文，供历史记录、任务列表和埋点分析复用。
    """
    
    stock_code: Optional[str] = Field(
        None, 
        description="单只股票代码", 
        example="600519"
    )
    stock_codes: Optional[List[str]] = Field(
        None, 
        description="多只股票代码（与 stock_code 二选一）",
        example=["600519", "000858"]
    )
    report_type: str = Field(
        "detailed",
        description="报告类型：simple(精简) / detailed(完整) / full(完整) / brief(简洁)",
        pattern="^(simple|detailed|full|brief)$",
    )
    force_refresh: bool = Field(
        False,
        description="是否强制刷新（忽略缓存）"
    )
    async_mode: bool = Field(
        False,
        description="是否使用异步模式"
    )
    stock_name: Optional[str] = Field(
        None,
        description="用户选中的股票名称（自动补全时提供）",
        example="贵州茅台"
    )
    original_query: Optional[str] = Field(
        None,
        description="用户原始输入（如茅台、gzmt、600519）",
        example="茅台"
    )
    selection_source: Optional[str] = Field(
        None,
        description="股票选择来源：manual(手动输入) | autocomplete(自动补全) | import(导入) | image(图片识别)",
        pattern=SELECTION_SOURCE_PATTERN,
        example="autocomplete"
    )
    notify: bool = Field(
        True,
        description="是否发送推送通知（Telegram/企业微信等）"
    )
    skills: Optional[List[str]] = Field(
        None,
        description="Analysis skill IDs for this request",
        example=["bull_trend", "growth_quality"]
    )

    class Config:
        """Document OpenAPI example metadata for analysis request schema."""
        json_schema_extra = {
            "example": {
                "stock_code": "600519",
                "report_type": "detailed",
                "force_refresh": False,
                "async_mode": False,
                "stock_name": "贵州茅台",
                "original_query": "茅台",
                "selection_source": "autocomplete",
                "notify": True,
                "skills": ["bull_trend"]
            }
        }


class MarketReviewRequest(BaseModel):
    """Request body for manually triggering a market-review background task."""

    send_notification: bool = Field(
        True,
        description="是否在大盘复盘完成后发送推送通知",
    )


class MarketReviewAccepted(BaseModel):
    """Response returned after a market-review task is accepted or skipped."""

    status: str = Field("accepted", description="提交状态")
    message: str = Field(..., description="提示信息")
    send_notification: bool = Field(..., description="是否发送通知")
    task_id: Optional[str] = Field(
        None,
        description="任务 ID（仅当任务实际提交时返回）",
    )


class AnalysisResultResponse(BaseModel):
    """Persisted analysis result returned by synchronous APIs or task polling."""
    
    query_id: str = Field(..., description="分析记录唯一标识")
    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    report: Optional[Any] = Field(None, description="分析报告")
    created_at: str = Field(..., description="创建时间")
    
    class Config:
        """Document OpenAPI example metadata for analysis response schema."""
        json_schema_extra = {
            "example": {
                "query_id": "abc123def456",
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "report": {
                    "summary": {
                        "sentiment_score": 75,
                        "operation_advice": "持有"
                    }
                },
                "created_at": "2024-01-01T12:00:00"
            }
        }


class TaskAccepted(BaseModel):
    """Response for a newly accepted asynchronous analysis task."""
    
    task_id: str = Field(..., description="任务 ID，用于查询状态")
    status: str = Field(
        ..., 
        description="任务状态",
        pattern="^(pending|processing)$"
    )
    message: Optional[str] = Field(None, description="提示信息")
    
    class Config:
        """Document OpenAPI example metadata for task acceptance schema."""
        json_schema_extra = {
            "example": {
                "task_id": "task_abc123",
                "status": "pending",
                "message": "Analysis task accepted"
            }
        }


class BatchTaskAcceptedItem(BaseModel):
    """One successfully submitted task in a batch-analysis request."""

    task_id: str = Field(..., description="任务 ID，用于查询状态")
    stock_code: str = Field(..., description="股票代码")
    status: str = Field(
        ...,
        description="任务状态",
        pattern="^(pending|processing)$"
    )
    message: Optional[str] = Field(None, description="提示信息")

    class Config:
        """Document OpenAPI example metadata for batch accepted item schema."""
        json_schema_extra = {
            "example": {
                "task_id": "task_abc123",
                "stock_code": "600519",
                "status": "pending",
                "message": "分析任务已加入队列: 600519"
            }
        }


class BatchDuplicateTaskItem(BaseModel):
    """One skipped duplicate item in a batch-analysis request."""

    stock_code: str = Field(..., description="股票代码")
    existing_task_id: str = Field(..., description="已存在的任务 ID")
    message: str = Field(..., description="错误信息")

    class Config:
        """Document OpenAPI example metadata for duplicate task item schema."""
        json_schema_extra = {
            "example": {
                "stock_code": "600519",
                "existing_task_id": "task_existing_123",
                "message": "股票 600519 正在分析中 (task_id: task_existing_123)"
            }
        }


class BatchTaskAcceptedResponse(BaseModel):
    """Batch-analysis submission summary with accepted and duplicate items."""

    accepted: List[BatchTaskAcceptedItem] = Field(default_factory=list, description="成功提交的任务列表")
    duplicates: List[BatchDuplicateTaskItem] = Field(default_factory=list, description="重复而跳过的任务列表")
    message: str = Field(..., description="汇总信息")

    class Config:
        """Document OpenAPI example metadata for batch submit response schema."""
        json_schema_extra = {
            "example": {
                "accepted": [
                    {
                        "task_id": "task_abc123",
                        "stock_code": "600519",
                        "status": "pending",
                        "message": "分析任务已加入队列: 600519"
                    }
                ],
                "duplicates": [
                    {
                        "stock_code": "000858",
                        "existing_task_id": "task_existing_456",
                        "message": "股票 000858 正在分析中 (task_id: task_existing_456)"
                    }
                ],
                "message": "已提交 1 个任务，1 个重复跳过"
            }
        }


class TaskStatus(BaseModel):
    """Task polling response for analysis and market-review jobs.

    ``result`` is present for completed stock-analysis jobs, while
    ``market_review_report`` is used by market-review jobs. Failed tasks carry
    ``error`` and normally leave both result fields empty.
    """
    
    task_id: str = Field(..., description="任务 ID")
    status: str = Field(
        ..., 
        description="任务状态",
        pattern="^(pending|processing|completed|failed)$"
    )
    progress: Optional[int] = Field(
        None, 
        description="进度百分比 (0-100)",
        ge=0,
        le=100
    )
    result: Optional[AnalysisResultResponse] = Field(
        None, 
        description="分析结果（仅在 completed 时存在）"
    )
    market_review_report: Optional[str] = Field(
        None,
        description="大盘复盘任务返回的报告文本（仅大盘复盘任务）",
    )
    error: Optional[str] = Field(
        None, 
        description="错误信息（仅在 failed 时存在）"
    )
    stock_name: Optional[str] = Field(None, description="股票名称")
    original_query: Optional[str] = Field(None, description="用户原始输入")
    selection_source: Optional[str] = Field(
        None,
        description="选择来源",
        pattern=SELECTION_SOURCE_PATTERN,
    )
    skills: Optional[List[str]] = Field(None, description="本次任务使用的策略 skill ID 列表")
    
    class Config:
        """Document OpenAPI example metadata for task status schema."""
        json_schema_extra = {
            "example": {
                "task_id": "task_abc123",
                "status": "completed",
                "progress": 100,
                "result": None,
                "market_review_report": None,
                "error": None,
                "stock_name": "贵州茅台",
                "original_query": "茅台",
                "selection_source": "autocomplete",
                "skills": ["bull_trend"]
            }
        }


class TaskInfo(BaseModel):
    """Task list/SSE payload carrying current execution metadata."""
    
    task_id: str = Field(..., description="任务 ID")
    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    status: TaskStatusEnum = Field(..., description="任务状态")
    progress: int = Field(0, description="进度百分比 (0-100)", ge=0, le=100)
    message: Optional[str] = Field(None, description="状态消息")
    report_type: str = Field("detailed", description="报告类型")
    created_at: str = Field(..., description="创建时间")
    started_at: Optional[str] = Field(None, description="开始执行时间")
    completed_at: Optional[str] = Field(None, description="完成时间")
    error: Optional[str] = Field(None, description="错误信息（仅在 failed 时存在）")
    original_query: Optional[str] = Field(None, description="用户原始输入")
    selection_source: Optional[str] = Field(
        None,
        description="选择来源",
        pattern=SELECTION_SOURCE_PATTERN,
    )
    skills: Optional[List[str]] = Field(None, description="本次任务使用的策略 skill ID 列表")
    
    class Config:
        """Document OpenAPI example metadata for task info schema."""
        json_schema_extra = {
            "example": {
                "task_id": "abc123def456",
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "status": "processing",
                "progress": 50,
                "message": "正在分析中...",
                "report_type": "detailed",
                "created_at": "2026-02-05T10:30:00",
                "started_at": "2026-02-05T10:30:01",
                "completed_at": None,
                "error": None,
                "original_query": "茅台",
                "selection_source": "autocomplete",
                "skills": ["bull_trend"]
            }
        }


class TaskListResponse(BaseModel):
    """Aggregated queue snapshot returned by task-list endpoints."""
    
    total: int = Field(..., description="任务总数")
    pending: int = Field(..., description="等待中的任务数")
    processing: int = Field(..., description="处理中的任务数")
    tasks: List[TaskInfo] = Field(..., description="任务列表")
    
    class Config:
        """Document OpenAPI example metadata for task list schema."""
        json_schema_extra = {
            "example": {
                "total": 3,
                "pending": 1,
                "processing": 2,
                "tasks": []
            }
        }


class DuplicateTaskErrorResponse(BaseModel):
    """Error payload returned when a single-stock task is already running."""
    
    error: str = Field("duplicate_task", description="错误类型")
    message: str = Field(..., description="错误信息")
    stock_code: str = Field(..., description="股票代码")
    existing_task_id: str = Field(..., description="已存在的任务 ID")
    
    class Config:
        """Document OpenAPI example metadata for duplicate task error schema."""
        json_schema_extra = {
            "example": {
                "error": "duplicate_task",
                "message": "股票 600519 正在分析中",
                "stock_code": "600519",
                "existing_task_id": "abc123def456"
            }
        }
