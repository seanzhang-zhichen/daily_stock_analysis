# -*- coding: utf-8 -*-
"""运营/管理员撰写的付费研报相关服务函数。

本模块刻意不介入事务（transaction-neutral）：调用方负责 SQLAlchemy 会话的
commit/rollback。这些函数负责：归一化 API 载荷、保证购买的幂等性，以及序列化
与当前用户相关的解锁与互动状态。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.storage import (
    AppResearchReport,
    AppResearchReportComment,
    AppResearchReportPurchase,
    AppResearchReportReaction,
    AppUser,
)
from src.users.credits import consume_credits


# 积分流水的 related_type 与 kind 统一用该常量，便于按研报消费做统计
RESEARCH_RELATED_TYPE = "research_report"


def _tags_to_json(tags: Optional[list[str]]) -> Optional[str]:
    """把标签列表规范化后存成 JSON 字符串：单标签截断 32 字符，最多保留 12 个。"""
    if tags is None:
        return None
    normalized = [str(tag).strip()[:32] for tag in tags if str(tag).strip()]
    return json.dumps(normalized[:12], ensure_ascii=False)


def _tags_from_json(value: Optional[str]) -> list[str]:
    """解码存储的标签 JSON，用于 API 输出，做防御性处理。"""
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if str(item).strip()]


def user_has_unlocked(db: Session, report: AppResearchReport, user: Optional[AppUser]) -> bool:
    """返回当前用户是否可以看到付费研报的完整正文。"""
    if int(report.price_credits or 0) <= 0:
        return True
    if user is None:
        return False
    exists = (
        db.query(AppResearchReportPurchase.id)
        .filter(
            AppResearchReportPurchase.report_id == int(report.id),
            AppResearchReportPurchase.user_id == int(user.id),
        )
        .first()
    )
    return exists is not None


def get_reaction_counts(db: Session, report_id: int) -> dict[str, int]:
    """统计单篇研报的点赞/点踩数量，返回 ``{"likes": n, "dislikes": n}``。"""
    rows = (
        db.query(AppResearchReportReaction.reaction, func.count(AppResearchReportReaction.id))
        .filter(AppResearchReportReaction.report_id == int(report_id))
        .group_by(AppResearchReportReaction.reaction)
        .all()
    )
    counts = {"likes": 0, "dislikes": 0}
    for reaction, count in rows:
        if reaction == "like":
            counts["likes"] = int(count or 0)
        elif reaction == "dislike":
            counts["dislikes"] = int(count or 0)
    return counts


def get_user_reaction(db: Session, report_id: int, user: Optional[AppUser]) -> Optional[str]:
    """返回当前用户在该研报上的表态（like/dislike/None）。"""
    if user is None:
        return None
    row = (
        db.query(AppResearchReportReaction.reaction)
        .filter(
            AppResearchReportReaction.report_id == int(report_id),
            AppResearchReportReaction.user_id == int(user.id),
        )
        .first()
    )
    return row[0] if row else None


def count_visible_comments(db: Session, report_id: int) -> int:
    """统计在公开研报详情中应当可见的评论数量。"""
    return (
        db.query(AppResearchReportComment)
        .filter(
            AppResearchReportComment.report_id == int(report_id),
            AppResearchReportComment.status == "visible",
        )
        .count()
    )


def serialize_report(
    db: Session,
    report: AppResearchReport,
    *,
    user: Optional[AppUser] = None,
    include_full: bool = False,
) -> dict:
    """序列化研报元数据，并附带当前用户的解锁态与点赞态。

    Args:
        db: 数据库会话。
        report: 研报实体。
        user: 当前用户；匿名时解锁态与 myReaction 均为空。
        include_full: 是否输出正文；未解锁时正文强制为 None，防止内容泄露。

    Returns:
        驼峰字段的 API 响应字典。
    """
    unlocked = user_has_unlocked(db, report, user)
    reactions = get_reaction_counts(db, int(report.id))
    payload = {
        "id": int(report.id),
        "title": report.title,
        "summary": report.summary,
        "previewContent": report.preview_content,
        "category": report.category,
        "tags": _tags_from_json(report.tags),
        "coverImageUrl": report.cover_image_url,
        "priceCredits": int(report.price_credits or 0),
        "isPublished": bool(report.is_published),
        "isUnlocked": unlocked,
        "likes": reactions["likes"],
        "dislikes": reactions["dislikes"],
        "commentsCount": count_visible_comments(db, int(report.id)),
        "myReaction": get_user_reaction(db, int(report.id), user),
        "publishedAt": report.published_at.isoformat() if report.published_at else None,
        "createdAt": report.created_at.isoformat() if report.created_at else None,
        "updatedAt": report.updated_at.isoformat() if report.updated_at else None,
    }
    if include_full:
        payload["fullContent"] = report.full_content if unlocked else None
    return payload


def create_report(
    db: Session,
    *,
    author: AppUser,
    title: str,
    summary: str,
    preview_content: str,
    full_content: str,
    price_credits: int,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
    cover_image_url: Optional[str] = None,
) -> AppResearchReport:
    """由运营/管理员创建一篇研报草稿。"""
    report = AppResearchReport(
        title=title.strip(),
        summary=summary.strip(),
        preview_content=preview_content.strip(),
        full_content=full_content.strip(),
        price_credits=max(0, int(price_credits or 0)),
        category=(category or "").strip()[:64] or None,
        tags=_tags_to_json(tags),
        cover_image_url=(cover_image_url or "").strip()[:1024] or None,
        author_id=int(author.id),
        is_published=False,
    )
    db.add(report)
    db.flush()
    return report


def update_report(
    db: Session,
    report: AppResearchReport,
    *,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    preview_content: Optional[str] = None,
    full_content: Optional[str] = None,
    price_credits: Optional[int] = None,
    category: Optional[str] = None,
    tags: Optional[list[str]] = None,
    cover_image_url: Optional[str] = None,
) -> AppResearchReport:
    """对已有研报做局部更新：仅更新显式传入（非 None）的字段。"""
    if title is not None:
        report.title = title.strip()
    if summary is not None:
        report.summary = summary.strip()
    if preview_content is not None:
        report.preview_content = preview_content.strip()
    if full_content is not None:
        report.full_content = full_content.strip()
    if price_credits is not None:
        report.price_credits = max(0, int(price_credits or 0))
    if category is not None:
        report.category = category.strip()[:64] or None
    if tags is not None:
        report.tags = _tags_to_json(tags)
    if cover_image_url is not None:
        report.cover_image_url = cover_image_url.strip()[:1024] or None
    db.add(report)
    db.flush()
    return report


def publish_report(db: Session, report: AppResearchReport) -> AppResearchReport:
    """发布一篇研报，并在首次发布时记录发布时间。"""
    report.is_published = True
    report.published_at = report.published_at or datetime.now()
    db.add(report)
    db.flush()
    return report


def unpublish_report(db: Session, report: AppResearchReport) -> AppResearchReport:
    """将研报从公开列表中下架，但不删除购买记录。"""
    report.is_published = False
    db.add(report)
    db.flush()
    return report


def purchase_report(db: Session, *, report: AppResearchReport, user: AppUser) -> AppResearchReportPurchase:
    """解锁一篇付费研报，并通过幂等键扣减积分。"""
    existing = (
        db.query(AppResearchReportPurchase)
        .filter(
            AppResearchReportPurchase.report_id == int(report.id),
            AppResearchReportPurchase.user_id == int(user.id),
        )
        .first()
    )
    if existing is not None:
        return existing

    price = max(0, int(report.price_credits or 0))
    ledger = None
    if price > 0:
        ledger = consume_credits(
            db,
            user=user,
            amount=price,
            kind=RESEARCH_RELATED_TYPE,
            related_type=RESEARCH_RELATED_TYPE,
            related_id=str(report.id),
            idempotency_key=f"research-report:{report.id}:user:{user.id}",
            note=f"unlock:{report.title[:80]}",
        )
    purchase = AppResearchReportPurchase(
        report_id=int(report.id),
        user_id=int(user.id),
        price_credits=price,
        ledger_id=int(ledger.id) if ledger is not None and ledger.id is not None else None,
    )
    db.add(purchase)
    db.flush()
    return purchase


def set_reaction(
    db: Session,
    *,
    report: AppResearchReport,
    user: AppUser,
    reaction: Optional[str],
) -> Optional[AppResearchReportReaction]:
    """设置用户的点赞/点踩：reaction 为 None 表示取消表态。

    Raises:
        ValueError: reaction 既不是 None 也不是 like/dislike。
    """
    row = (
        db.query(AppResearchReportReaction)
        .filter(
            AppResearchReportReaction.report_id == int(report.id),
            AppResearchReportReaction.user_id == int(user.id),
        )
        .first()
    )
    if reaction is None:
        if row is not None:
            db.delete(row)
            db.flush()
        return None
    if reaction not in ("like", "dislike"):
        raise ValueError("reaction must be like or dislike")
    if row is None:
        row = AppResearchReportReaction(
            report_id=int(report.id),
            user_id=int(user.id),
            reaction=reaction,
        )
    else:
        row.reaction = reaction
    db.add(row)
    db.flush()
    return row


def add_comment(
    db: Session,
    *,
    report: AppResearchReport,
    user: AppUser,
    content: str,
) -> AppResearchReportComment:
    """为研报添加一条默认可见的用户评论。"""
    comment = AppResearchReportComment(
        report_id=int(report.id),
        user_id=int(user.id),
        content=content.strip(),
        status="visible",
    )
    db.add(comment)
    db.flush()
    return comment


def serialize_comment(comment: AppResearchReportComment, user: Optional[AppUser] = None) -> dict:
    """序列化单条研报评论；传入 user 时附带作者邮箱。"""
    return {
        "id": int(comment.id),
        "reportId": int(comment.report_id),
        "userId": int(comment.user_id),
        "authorEmail": getattr(user, "email", None),
        "content": comment.content,
        "createdAt": comment.created_at.isoformat() if comment.created_at else None,
    }
