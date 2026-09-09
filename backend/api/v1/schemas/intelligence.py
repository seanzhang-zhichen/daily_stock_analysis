"""情报池（Intelligence Pool）模块的对外 API 契约。

该 schema 描述 A 股本地情报池的：情报源配置、模板、默认源创建结果、
抓取任务结果以及情报条目查询响应。所有字段保持与服务层一致，便于前端表格
与下拉控件渲染。
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class IntelligenceSourceCreateRequest(BaseModel):
    """新建一条情报源的请求载荷。"""

    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=1, max_length=1000)
    source_type: str = "rss"
    enabled: bool = True
    scope_type: str = "market"
    scope_value: Optional[str] = None
    market: str = "cn"
    description: Optional[str] = None


class IntelligenceSourceEnabledRequest(BaseModel):
    enabled: bool


class IntelligenceSourceItem(BaseModel):
    """情报源条目：包含基础配置、最近抓取状态与最近一次错误信息。"""

    id: int; name: str; source_type: str; url: str; enabled: bool; scope_type: str; scope_value: Optional[str] = None; market: str; description: Optional[str] = None; last_status: Optional[str] = None; last_error: Optional[str] = None


class IntelligenceSourceTemplateItem(BaseModel):
    """情报源模板条目：用于快速复制创建预置源。"""

    template_id: str; name: str; source_type: str; url: str; scope_type: str; market: str; description: Optional[str] = None


class IntelligenceSourceListResponse(BaseModel):
    """情报源分页查询响应。"""

    items: List[IntelligenceSourceItem] = []; total: int; page: int; page_size: int


class IntelligenceSourceTemplateListResponse(BaseModel):
    """情报源模板列表响应。"""

    items: List[IntelligenceSourceTemplateItem] = []; total: int


class IntelligenceDefaultSourceCreateResponse(BaseModel):
    """批量创建默认情报源的执行结果响应。"""

    items: List[Dict[str, Any]] = []; created_count: int; total: int


class IntelligenceItem(BaseModel):
    """单条情报条目：包含来源、标题、摘要、发布时间与作用范围等元数据。"""

    id: int; source_id: Optional[int] = None; source_name: Optional[str] = None; source_type: str; title: str; summary: Optional[str] = None; url: str; source: Optional[str] = None; published_at: Optional[str] = None; fetched_at: Optional[str] = None; scope_type: str; scope_value: Optional[str] = None; market: str


class IntelligenceItemListResponse(BaseModel):
    """情报条目分页查询响应。"""

    items: List[IntelligenceItem] = []; total: int; page: int; page_size: int


class IntelligenceFetchResponse(BaseModel):
    """单次情报抓取任务的执行结果响应（含 dry-run 与逐源详情）。"""

    ok: bool; source_id: Optional[int] = None; source_count: Optional[int] = None; fetched_count: Optional[int] = None; saved_count: Optional[int] = None; dry_run: Optional[bool] = None; results: Optional[List[dict]] = None; error: Optional[str] = None
