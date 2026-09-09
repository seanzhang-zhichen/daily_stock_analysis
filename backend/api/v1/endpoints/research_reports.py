# -*- coding: utf-8 -*-
"""付费研报相关接口。

公开端允许浏览已发布研报、购买解锁全文、点赞/点踩和评论；运营端允许具备
``is_research_operator`` 身份的用户维护自己的研报。购买、互动和序列化逻辑集中在
``research_report_service`` 中，endpoint 层负责权限与 HTTP 错误转换。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_current_user, get_db, get_optional_current_user
from src.services.research_report_service import (
    add_comment,
    create_report,
    publish_report,
    purchase_report,
    serialize_comment,
    serialize_report,
    set_reaction,
    unpublish_report,
    update_report,
)
from src.storage import (
    AppResearchReport,
    AppResearchReportComment,
    AppResearchReportPurchase,
    AppResearchReportReaction,
    AppUser,
)
from src.users.audit import write_audit_log


router = APIRouter()


class ResearchReportCreateRequest(BaseModel):
    """研报运营者创建报告草稿的请求体。"""

    title: str = Field(..., min_length=2, max_length=255)
    summary: str = Field(..., min_length=2, max_length=4000)
    previewContent: str = Field(..., min_length=2)
    fullContent: str = Field(..., min_length=2)
    priceCredits: int = Field(default=0, ge=0, le=100000)
    category: Optional[str] = Field(default=None, max_length=64)
    tags: list[str] = Field(default_factory=list)
    coverImageUrl: Optional[str] = Field(default=None, max_length=1024)


class ResearchReportUpdateRequest(BaseModel):
    """研报运营者局部更新已有报告的请求体。"""

    title: Optional[str] = Field(default=None, min_length=2, max_length=255)
    summary: Optional[str] = Field(default=None, min_length=2, max_length=4000)
    previewContent: Optional[str] = Field(default=None, min_length=2)
    fullContent: Optional[str] = Field(default=None, min_length=2)
    priceCredits: Optional[int] = Field(default=None, ge=0, le=100000)
    category: Optional[str] = Field(default=None, max_length=64)
    tags: Optional[list[str]] = None
    coverImageUrl: Optional[str] = Field(default=None, max_length=1024)


class ReactionRequest(BaseModel):
    """点赞/点踩请求；值为 null 时清除当前用户的表态。"""

    reaction: Optional[str] = Field(default=None, pattern="^(like|dislike)$")


class CommentRequest(BaseModel):
    """已发布研报的可见用户评论载荷。"""

    content: str = Field(..., min_length=1, max_length=2000)


def _optional_user(request: Request) -> Optional[AppUser]:
    """解析可选登录用户，让公开接口能按查看者个性化输出。"""
    return get_optional_current_user(request)


def get_research_operator_user(current_user: AppUser = Depends(get_current_user)) -> AppUser:
    """校验当前登录用户具备研报运营身份，否则拒绝访问。"""
    # 用 getattr 兜底缺失属性的旧数据/不同账号体系，避免属性不存在时直接 500。
    if not bool(getattr(current_user, "is_research_operator", False)):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "需要研报运营身份"},
        )
    return current_user


def _get_report_or_404(db: Session, report_id: int) -> AppResearchReport:
    """按 ID 加载任意研报，不存在则抛出公开 404。"""
    report = db.query(AppResearchReport).filter(AppResearchReport.id == int(report_id)).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Research report not found")
    return report


def _get_published_report_or_404(db: Session, report_id: int) -> AppResearchReport:
    """加载已发布研报，草稿统一伪装成 404 以隐藏其存在。"""
    report = _get_report_or_404(db, report_id)
    if not bool(report.is_published):
        raise HTTPException(status_code=404, detail="Research report not found")
    return report


@router.get("", summary="List published research reports")
def list_research_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: Optional[str] = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
    current_user: Optional[AppUser] = Depends(_optional_user),
) -> dict:
    """列出已发布研报，并可按当前查看者个性化输出。"""
    query = db.query(AppResearchReport).filter(AppResearchReport.is_published == True)  # noqa: E712
    if category:
        query = query.filter(AppResearchReport.category == category)
    total = query.count()
    reports = (
        query.order_by(AppResearchReport.published_at.desc(), AppResearchReport.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "reports": [
            serialize_report(db, report, user=current_user, include_full=False)
            for report in reports
        ],
        "count": total,
    }


@router.get("/mine/list", summary="Research operator list own reports")
def operator_list_research_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    operator: AppUser = Depends(get_research_operator_user),
) -> dict:
    """列出当前研报运营者本人创作的报告。"""
    query = db.query(AppResearchReport).filter(AppResearchReport.author_id == int(operator.id))
    total = query.count()
    reports = (
        query.order_by(AppResearchReport.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "reports": [serialize_report(db, report, user=operator, include_full=True) for report in reports],
        "count": total,
    }


@router.get("/{report_id}", summary="Get a published research report")
def get_research_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[AppUser] = Depends(_optional_user),
) -> dict:
    """返回单篇已发布研报；用户具备权限时才包含全文内容。"""
    report = _get_published_report_or_404(db, report_id)
    return {"report": serialize_report(db, report, user=current_user, include_full=True)}


@router.post("/{report_id}/purchase", summary="Unlock a research report with credits")
def unlock_research_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """使用当前用户积分购买/解锁一篇已发布研报。"""
    report = _get_published_report_or_404(db, report_id)
    user = db.query(AppUser).filter(AppUser.id == int(current_user.id)).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    try:
        purchase = purchase_report(db, report=report, user=user)
        db.commit()
        db.refresh(report)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=402,
            detail=str(exc),
            headers={"X-DSA-Error": "credit_exceeded"},
        ) from exc
    write_audit_log(
        db,
        "research_report.purchase",
        user_id=int(user.id),
        target_ref=str(report.id),
        detail={"priceCredits": purchase.price_credits},
    )
    db.commit()
    return {
        "purchase": {
            "id": int(purchase.id),
            "reportId": int(purchase.report_id),
            "priceCredits": int(purchase.price_credits or 0),
            "purchasedAt": purchase.purchased_at.isoformat() if purchase.purchased_at else None,
        },
        "report": serialize_report(db, report, user=user, include_full=True),
        "creditBalance": int(user.credit_balance or 0),
    }


@router.post("/{report_id}/reaction", summary="Like or dislike a research report")
def react_research_report(
    report_id: int,
    body: ReactionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """设置、修改或清除当前用户对研报的表态。"""
    report = _get_published_report_or_404(db, report_id)
    user = db.query(AppUser).filter(AppUser.id == int(current_user.id)).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    try:
        set_reaction(db, report=report, user=user, reaction=body.reaction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(report)
    return {"report": serialize_report(db, report, user=user, include_full=True)}


@router.get("/{report_id}/comments", summary="List visible research report comments")
def list_comments(
    report_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """列出一篇已发布研报的可见评论。"""
    report = _get_published_report_or_404(db, report_id)
    rows = (
        db.query(AppResearchReportComment, AppUser)
        .join(AppUser, AppUser.id == AppResearchReportComment.user_id)
        .filter(
            AppResearchReportComment.report_id == int(report.id),
            AppResearchReportComment.status == "visible",
        )
        .order_by(AppResearchReportComment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    total = (
        db.query(AppResearchReportComment)
        .filter(
            AppResearchReportComment.report_id == int(report.id),
            AppResearchReportComment.status == "visible",
        )
        .count()
    )
    return {"comments": [serialize_comment(comment, user) for comment, user in rows], "count": total}


@router.post("/{report_id}/comments", summary="Create a research report comment")
def create_comment(
    report_id: int,
    body: CommentRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """在已发布研报下创建一条可见评论。"""
    report = _get_published_report_or_404(db, report_id)
    user = db.query(AppUser).filter(AppUser.id == int(current_user.id)).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    comment = add_comment(db, report=report, user=user, content=body.content)
    db.commit()
    db.refresh(comment)
    return {"comment": serialize_comment(comment, user)}


def _get_operator_report_or_404(db: Session, report_id: int, operator: AppUser) -> AppResearchReport:
    """加载运营者本人所有的研报，他人的研报统一伪装成 404。"""
    report = _get_report_or_404(db, report_id)
    if int(report.author_id or 0) != int(operator.id):
        raise HTTPException(status_code=404, detail="Research report not found")
    return report


@router.post("", summary="Research operator create report", status_code=201)
def operator_create_research_report(
    body: ResearchReportCreateRequest,
    db: Session = Depends(get_db),
    operator: AppUser = Depends(get_research_operator_user),
) -> dict:
    """创建归运营者所有的研报草稿。"""
    report = create_report(
        db,
        author=operator,
        title=body.title,
        summary=body.summary,
        preview_content=body.previewContent,
        full_content=body.fullContent,
        price_credits=body.priceCredits,
        category=body.category,
        tags=body.tags,
        cover_image_url=body.coverImageUrl,
    )
    db.commit()
    db.refresh(report)
    return {"report": serialize_report(db, report, user=operator, include_full=True)}


@router.patch("/{report_id}", summary="Research operator update own report")
def operator_update_research_report(
    report_id: int,
    body: ResearchReportUpdateRequest,
    db: Session = Depends(get_db),
    operator: AppUser = Depends(get_research_operator_user),
) -> dict:
    """更新运营者本人所有的研报。"""
    report = _get_operator_report_or_404(db, report_id, operator)
    report = update_report(
        db,
        report,
        title=body.title,
        summary=body.summary,
        preview_content=body.previewContent,
        full_content=body.fullContent,
        price_credits=body.priceCredits,
        category=body.category,
        tags=body.tags,
        cover_image_url=body.coverImageUrl,
    )
    db.commit()
    db.refresh(report)
    return {"report": serialize_report(db, report, user=operator, include_full=True)}


@router.delete("/{report_id}", summary="Research operator delete own report", status_code=204)
def operator_delete_research_report(
    report_id: int,
    db: Session = Depends(get_db),
    operator: AppUser = Depends(get_research_operator_user),
) -> Response:
    """仅在研报无任何关联记录时删除运营者本人所有的报告。"""
    report = _get_operator_report_or_404(db, report_id, operator)
    # 已有购买/表态/评论的研报不可物理删除，否则破坏外键与用户已购权益，需引导改为下架。
    related_count = (
        db.query(AppResearchReportPurchase.id)
        .filter(AppResearchReportPurchase.report_id == int(report.id))
        .count()
        + db.query(AppResearchReportReaction.id)
        .filter(AppResearchReportReaction.report_id == int(report.id))
        .count()
        + db.query(AppResearchReportComment.id)
        .filter(AppResearchReportComment.report_id == int(report.id))
        .count()
    )
    if related_count > 0:
        raise HTTPException(status_code=400, detail="研报已有购买或互动记录，请改为下架")
    db.delete(report)
    db.commit()
    return Response(status_code=204)


@router.post("/{report_id}/publish", summary="Research operator publish own report")
def operator_publish_research_report(
    report_id: int,
    db: Session = Depends(get_db),
    operator: AppUser = Depends(get_research_operator_user),
) -> dict:
    """发布运营者本人所有的研报。"""
    report = publish_report(db, _get_operator_report_or_404(db, report_id, operator))
    db.commit()
    db.refresh(report)
    return {"report": serialize_report(db, report, user=operator, include_full=True)}


@router.post("/{report_id}/unpublish", summary="Research operator unpublish own report")
def operator_unpublish_research_report(
    report_id: int,
    db: Session = Depends(get_db),
    operator: AppUser = Depends(get_research_operator_user),
) -> dict:
    """下架运营者本人所有的研报。"""
    report = unpublish_report(db, _get_operator_report_or_404(db, report_id, operator))
    db.commit()
    db.refresh(report)
    return {"report": serialize_report(db, report, user=operator, include_full=True)}
