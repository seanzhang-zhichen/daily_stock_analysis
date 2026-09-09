"""A 股本地情报池管理的 HTTP 路由。

提供情报源模板查询、默认源批量创建、自定义源增删与查询、单源抓取、
批量抓取已启用源以及情报条目列表检索等接口。底层统一委托 `IntelligenceService`。
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from api.deps import get_admin_user
from api.v1.schemas.intelligence import *
from src.services.intelligence_service import IntelligenceService, IntelligenceServiceError

router = APIRouter(dependencies=[Depends(get_admin_user)])

def _error(exc):
    """将业务异常统一包装为 400 校验错误响应。"""
    raise HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)})

@router.get("/sources/templates", response_model=IntelligenceSourceTemplateListResponse)
def templates():
    """返回预置的情报源模板列表，供前端"一键添加"使用。"""
    return IntelligenceService().list_source_templates()

@router.post("/sources/defaults", response_model=IntelligenceDefaultSourceCreateResponse)
def defaults(enabled: bool = False):
    """批量创建平台默认情报源；``enabled`` 控制创建后是否立即启用。"""
    return IntelligenceService().create_default_sources(enabled=enabled)

@router.post("/sources", response_model=IntelligenceSourceItem)
def create_source(request: IntelligenceSourceCreateRequest):
    """创建一条自定义情报源。"""
    try: return IntelligenceService().create_source(request.model_dump())
    except IntelligenceServiceError as exc: _error(exc)

@router.get("/sources", response_model=IntelligenceSourceListResponse)
def list_sources(enabled: Optional[bool] = Query(None), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
    """分页查询情报源列表，支持按启用状态过滤。"""
    return IntelligenceService().list_sources(enabled=enabled, page=page, page_size=page_size)

@router.post("/sources/fetch-enabled", response_model=IntelligenceFetchResponse)
def fetch_enabled():
    """批量触发所有已启用情报源的抓取任务。"""
    return IntelligenceService().fetch_enabled_sources()

@router.patch("/sources/{source_id}", response_model=IntelligenceSourceItem)
def set_source_enabled(source_id: int, request: IntelligenceSourceEnabledRequest):
    try:
        return IntelligenceService().set_source_enabled(source_id, request.enabled)
    except IntelligenceServiceError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
        _error(exc)

@router.delete("/sources/{source_id}")
def delete_source(source_id: int):
    try:
        return IntelligenceService().delete_source(source_id)
    except IntelligenceServiceError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc

@router.post("/sources/{source_id}/fetch", response_model=IntelligenceFetchResponse)
def fetch_source(source_id: int, dry_run: bool = False):
    """触发指定情报源的一次抓取任务；``dry_run=True`` 时不写入数据库。"""
    try: return IntelligenceService().fetch_source(source_id, dry_run=dry_run)
    except IntelligenceServiceError as exc: _error(exc)

@router.get("/items", response_model=IntelligenceItemListResponse)
def list_items(scope_type: Optional[str] = None, scope_value: Optional[str] = None, query: Optional[str] = None, days: Optional[int] = Query(None, ge=1), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
    """分页查询已入库的情报条目，支持按作用范围/关键字/天数过滤。"""
    return IntelligenceService().list_items(scope_type=scope_type, scope_value=scope_value, query=query, days=days, page=page, page_size=page_size)
