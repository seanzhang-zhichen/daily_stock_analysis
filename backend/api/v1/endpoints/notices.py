# -*- coding: utf-8 -*-
"""公告中心 API 端点（Notice Center endpoints）。

本模块提供用户端公开公告读取接口与管理端 CRUD 接口。

- 用户端接口（无需登录）：列出已发布且未过期的公告、返回近期未读角标数量。
- 管理员接口（依赖 ``get_admin_user``）：草稿创建、发布、下架与删除等写操作。

所有公告内容在前端使用 camelCase 字段命名，因此 ORM 行通过 ``_to_notice_out``
统一序列化为 ``NoticeOut`` 后再返回。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_admin_user, get_db
from src.storage import AppNotice, AppUser

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class NoticeOut(BaseModel):
    """返回给前端的公告 DTO（Data Transfer Object），使用 camelCase 字段命名。"""

    id: int
    title: str
    content: str
    noticeType: str
    isPinned: bool
    isPublished: bool
    targetPlan: Optional[str]
    publishedAt: Optional[str]
    expiresAt: Optional[str]
    createdAt: str

    model_config = {"from_attributes": True}


def _to_notice_out(n: AppNotice) -> NoticeOut:
    """将 ORM 行 ``AppNotice`` 序列化为前端约定的 camelCase 结构。"""
    return NoticeOut(
        id=n.id,
        title=n.title,
        content=n.content,
        noticeType=n.notice_type,
        isPinned=n.is_pinned,
        isPublished=n.is_published,
        targetPlan=n.target_plan,
        publishedAt=n.published_at.isoformat() if n.published_at else None,
        expiresAt=n.expires_at.isoformat() if n.expires_at else None,
        createdAt=n.created_at.isoformat(),
    )


class NoticeCreateRequest(BaseModel):
    """管理员创建公告草稿的请求体。"""

    title: str = Field(..., max_length=255)
    content: str
    noticeType: str = Field(default="info", pattern="^(info|warning|danger)$")
    isPinned: bool = False
    targetPlan: Optional[str] = None
    expiresAt: Optional[str] = None


class NoticeUpdateRequest(BaseModel):
    """管理员对公告（草稿或已发布）的局部更新请求体。"""

    title: Optional[str] = Field(default=None, max_length=255)
    content: Optional[str] = None
    noticeType: Optional[str] = Field(default=None, pattern="^(info|warning|danger)$")
    isPinned: Optional[bool] = None
    targetPlan: Optional[str] = None
    expiresAt: Optional[str] = None


# ---------------------------------------------------------------------------
# Public user-facing endpoints.
# ---------------------------------------------------------------------------


@router.get(
    "",
    summary="获取已发布公告列表",
    description="公开接口，返回已发布且未过期的公告，置顶优先，按发布时间倒序。",
    response_model=List[NoticeOut],
)
def list_notices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> List[NoticeOut]:
    """分页列出已发布且未过期的公告，置顶项优先并按发布时间倒序。"""
    now = datetime.now()
    # 已发布 且 未过期（expires_at 为空或晚于当前时间）
    query = (
        db.query(AppNotice)
        .filter(AppNotice.is_published == True)  # noqa: E712
        .filter(
            (AppNotice.expires_at == None) | (AppNotice.expires_at > now)  # noqa: E711
        )
        .order_by(AppNotice.is_pinned.desc(), AppNotice.published_at.desc())
    )
    offset = (page - 1) * page_size
    notices = query.offset(offset).limit(page_size).all()
    return [_to_notice_out(n) for n in notices]


@router.get(
    "/unread-count",
    summary="获取近期公告数量",
    description="返回最近 30 天内发布的已发布公告数量，用于铃铛角标。",
)
def get_unread_count(
    db: Session = Depends(get_db),
) -> dict:
    """返回最近 30 天内仍处于有效状态的已发布公告数量，用于顶部铃铛角标。"""
    # 仅统计最近 30 天发布的、且当前仍未过期的公告
    cutoff = datetime.now() - timedelta(days=30)
    now = datetime.now()
    count = (
        db.query(AppNotice)
        .filter(AppNotice.is_published == True)  # noqa: E712
        .filter(AppNotice.published_at >= cutoff)
        .filter(
            (AppNotice.expires_at == None) | (AppNotice.expires_at > now)  # noqa: E711
        )
        .count()
    )
    return {"count": count}


# ---------------------------------------------------------------------------
# Admin-only endpoints.
# ---------------------------------------------------------------------------


@router.get(
    "/admin/list",
    summary="（管理员）获取全量公告列表（含草稿）",
    response_model=List[NoticeOut],
)
def admin_list_notices(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: AppUser = Depends(get_admin_user),
) -> List[NoticeOut]:
    """管理员列出全量公告（含草稿与已下架项）。"""
    offset = (page - 1) * page_size
    notices = (
        db.query(AppNotice)
        .order_by(AppNotice.is_pinned.desc(), AppNotice.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    return [_to_notice_out(n) for n in notices]


@router.post(
    "",
    summary="（管理员）创建公告",
    response_model=NoticeOut,
    status_code=201,
)
def create_notice(
    body: NoticeCreateRequest,
    db: Session = Depends(get_db),
    admin: AppUser = Depends(get_admin_user),
) -> NoticeOut:
    """管理员创建一条未发布的公告草稿。"""
    expires = None
    # expiresAt 由前端以 ISO8601 字符串传入；解析失败时返回 422 提示格式问题
    if body.expiresAt:
        try:
            expires = datetime.fromisoformat(body.expiresAt)
        except ValueError:
            raise HTTPException(status_code=422, detail="expiresAt 格式无效")
    notice = AppNotice(
        title=body.title,
        content=body.content,
        notice_type=body.noticeType,
        is_pinned=body.isPinned,
        # 新建即为草稿状态，需调用 publish 接口才会对用户可见
        is_published=False,
        target_plan=body.targetPlan,
        author_id=admin.id,
        expires_at=expires,
    )
    db.add(notice)
    db.commit()
    db.refresh(notice)
    return _to_notice_out(notice)


@router.patch(
    "/{notice_id}",
    summary="（管理员）更新公告",
    response_model=NoticeOut,
)
def update_notice(
    notice_id: int,
    body: NoticeUpdateRequest,
    db: Session = Depends(get_db),
    _admin: AppUser = Depends(get_admin_user),
) -> NoticeOut:
    """管理员更新公告的内容、面向人群、置顶状态或过期时间。"""
    notice = db.query(AppNotice).filter(AppNotice.id == notice_id).first()
    if notice is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    # 仅写入请求体中显式给出的字段，避免把已有值覆盖为 None
    if body.title is not None:
        notice.title = body.title
    if body.content is not None:
        notice.content = body.content
    if body.noticeType is not None:
        notice.notice_type = body.noticeType
    if body.isPinned is not None:
        notice.is_pinned = body.isPinned
    if body.targetPlan is not None:
        notice.target_plan = body.targetPlan
    if body.expiresAt is not None:
        try:
            notice.expires_at = datetime.fromisoformat(body.expiresAt)
        except ValueError:
            raise HTTPException(status_code=422, detail="expiresAt 格式无效")
    db.commit()
    db.refresh(notice)
    return _to_notice_out(notice)


@router.delete(
    "/{notice_id}",
    summary="（管理员）删除公告",
    status_code=204,
    response_class=Response,
    response_model=None,
)
def delete_notice(
    notice_id: int,
    db: Session = Depends(get_db),
    _admin: AppUser = Depends(get_admin_user),
) -> None:
    """管理员永久删除一条公告。"""
    notice = db.query(AppNotice).filter(AppNotice.id == notice_id).first()
    if notice is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    db.delete(notice)
    db.commit()


@router.post(
    "/{notice_id}/publish",
    summary="（管理员）发布公告",
    response_model=NoticeOut,
)
def publish_notice(
    notice_id: int,
    db: Session = Depends(get_db),
    _admin: AppUser = Depends(get_admin_user),
) -> NoticeOut:
    """管理员发布公告，并写入发布时间戳。"""
    notice = db.query(AppNotice).filter(AppNotice.id == notice_id).first()
    if notice is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    notice.is_published = True
    # 发布时间是用户端排序与角标统计的关键字段
    notice.published_at = datetime.now()
    db.commit()
    db.refresh(notice)
    return _to_notice_out(notice)


@router.post(
    "/{notice_id}/unpublish",
    summary="（管理员）下架公告",
    response_model=NoticeOut,
)
def unpublish_notice(
    notice_id: int,
    db: Session = Depends(get_db),
    _admin: AppUser = Depends(get_admin_user),
) -> NoticeOut:
    """管理员下架公告（不删除记录，便于后续重新发布）。"""
    notice = db.query(AppNotice).filter(AppNotice.id == notice_id).first()
    if notice is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    notice.is_published = False
    db.commit()
    db.refresh(notice)
    return _to_notice_out(notice)
