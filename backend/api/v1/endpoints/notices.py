# -*- coding: utf-8 -*-
"""Notice center endpoints (Phase 6).

用户端公开读取已发布且未过期的公告；管理员端负责草稿、发布、下架和删除。
公告读取不要求登录，后台写操作通过 ``get_admin_user`` 保护。
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
    """Notice payload returned to frontend clients."""

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
    """Serialize notice ORM row using frontend camelCase field names."""
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
    """Admin request body for creating a notice draft."""

    title: str = Field(..., max_length=255)
    content: str
    noticeType: str = Field(default="info", pattern="^(info|warning|danger)$")
    isPinned: bool = False
    targetPlan: Optional[str] = None
    expiresAt: Optional[str] = None


class NoticeUpdateRequest(BaseModel):
    """Admin partial update body for a notice draft or published notice."""

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
    """List published, non-expired notices with pinned items first."""
    now = datetime.now()
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
    """Return recent published notice count for the header badge."""
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
    """List all notices, including drafts and unpublished items."""
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
    """Create an unpublished notice draft."""
    expires = None
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
    """Update notice content, targeting, pinning, or expiration."""
    notice = db.query(AppNotice).filter(AppNotice.id == notice_id).first()
    if notice is None:
        raise HTTPException(status_code=404, detail="公告不存在")
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
    """Delete a notice permanently."""
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
    """Publish a notice and stamp its publication time."""
    notice = db.query(AppNotice).filter(AppNotice.id == notice_id).first()
    if notice is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    notice.is_published = True
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
    """Unpublish a notice without deleting it."""
    notice = db.query(AppNotice).filter(AppNotice.id == notice_id).first()
    if notice is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    notice.is_published = False
    db.commit()
    db.refresh(notice)
    return _to_notice_out(notice)
