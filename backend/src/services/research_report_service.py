# -*- coding: utf-8 -*-
"""Service helpers for operator-authored paid research reports."""

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


RESEARCH_RELATED_TYPE = "research_report"


def _tags_to_json(tags: Optional[list[str]]) -> Optional[str]:
    if tags is None:
        return None
    normalized = [str(tag).strip()[:32] for tag in tags if str(tag).strip()]
    return json.dumps(normalized[:12], ensure_ascii=False)


def _tags_from_json(value: Optional[str]) -> list[str]:
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
    report.is_published = True
    report.published_at = report.published_at or datetime.now()
    db.add(report)
    db.flush()
    return report


def unpublish_report(db: Session, report: AppResearchReport) -> AppResearchReport:
    report.is_published = False
    db.add(report)
    db.flush()
    return report


def purchase_report(db: Session, *, report: AppResearchReport, user: AppUser) -> AppResearchReportPurchase:
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
    return {
        "id": int(comment.id),
        "reportId": int(comment.report_id),
        "userId": int(comment.user_id),
        "authorEmail": getattr(user, "email", None),
        "content": comment.content,
        "createdAt": comment.created_at.isoformat() if comment.created_at else None,
    }
