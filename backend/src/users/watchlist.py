# -*- coding: utf-8 -*-
"""用户自选股服务 (Phase 3)。

封装对 ``app_user_watchlists`` 表的 CRUD，并按 ``plan.max_stocks`` 检查上限。

用法示例::

    from src.users.watchlist import add_stock, list_stocks, remove_stock

    stocks = list_stocks(db, user_id=1)
    add_stock(db, user=user, stock_code="600519", stock_name="贵州茅台", plan=plan)
    remove_stock(db, user_id=1, stock_code="600519")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.storage import AppUser, AppUserWatchlist
from src.users.errors import UserError, UserErrorCode
from src.users.plans import ResolvedPlan


@dataclass(frozen=True)
class WatchlistItem:
    """自选股条目视图（只读）。"""

    stock_code: str
    stock_name: Optional[str]


def list_stocks(db: Session, *, user_id: int) -> List[WatchlistItem]:
    """返回用户当前自选股列表，按加入时间升序。"""
    rows = (
        db.query(AppUserWatchlist)
        .filter(AppUserWatchlist.user_id == user_id)
        .order_by(AppUserWatchlist.created_at.asc())
        .all()
    )
    return [WatchlistItem(stock_code=r.stock_code, stock_name=r.stock_name) for r in rows]


def count_stocks(db: Session, *, user_id: int) -> int:
    """返回用户当前自选股数量。"""
    return (
        db.query(AppUserWatchlist)
        .filter(AppUserWatchlist.user_id == user_id)
        .count()
    )


def add_stock(
    db: Session,
    *,
    user: AppUser,
    stock_code: str,
    stock_name: Optional[str] = None,
    plan: ResolvedPlan,
) -> WatchlistItem:
    """添加一只自选股。

    - 若已存在则幂等返回（不报错）。
    - 超出 ``plan.max_stocks`` 上限则抛出 :class:`UserError`。
    """
    code = (stock_code or "").strip().upper()
    if not code:
        raise UserError(UserErrorCode.VALIDATION_ERROR, "股票代码不能为空")

    existing = (
        db.query(AppUserWatchlist)
        .filter(
            AppUserWatchlist.user_id == user.id,
            AppUserWatchlist.stock_code == code,
        )
        .first()
    )
    if existing is not None:
        return WatchlistItem(stock_code=existing.stock_code, stock_name=existing.stock_name)

    current_count = count_stocks(db, user_id=user.id)
    if current_count >= plan.max_stocks:
        raise UserError(
            UserErrorCode.QUOTA_EXCEEDED,
            f"自选股已达上限 {plan.max_stocks} 只，请升级套餐或删除已有自选股",
        )

    row = AppUserWatchlist(
        user_id=user.id,
        stock_code=code,
        stock_name=(stock_name or "").strip() or None,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(AppUserWatchlist)
            .filter(
                AppUserWatchlist.user_id == user.id,
                AppUserWatchlist.stock_code == code,
            )
            .first()
        )
        if existing:
            return WatchlistItem(stock_code=existing.stock_code, stock_name=existing.stock_name)
        raise
    return WatchlistItem(stock_code=code, stock_name=row.stock_name)


def remove_stock(db: Session, *, user_id: int, stock_code: str) -> bool:
    """删除一只自选股，返回是否实际删除了行。"""
    code = (stock_code or "").strip().upper()
    deleted = (
        db.query(AppUserWatchlist)
        .filter(
            AppUserWatchlist.user_id == user_id,
            AppUserWatchlist.stock_code == code,
        )
        .delete(synchronize_session=False)
    )
    db.flush()
    return deleted > 0


def set_watchlist(
    db: Session,
    *,
    user: AppUser,
    stock_codes: List[str],
    stock_names: Optional[dict] = None,
    plan: ResolvedPlan,
) -> List[WatchlistItem]:
    """批量设置自选股（全量替换）。超出上限则抛出 :class:`UserError`。"""
    codes = list(dict.fromkeys((c or "").strip().upper() for c in stock_codes if (c or "").strip()))
    if len(codes) > plan.max_stocks:
        raise UserError(
            UserErrorCode.QUOTA_EXCEEDED,
            f"自选股数量 {len(codes)} 超出套餐上限 {plan.max_stocks} 只",
        )

    db.query(AppUserWatchlist).filter(AppUserWatchlist.user_id == user.id).delete(
        synchronize_session=False
    )
    rows = []
    names = stock_names or {}
    for code in codes:
        row = AppUserWatchlist(
            user_id=user.id,
            stock_code=code,
            stock_name=(names.get(code) or "").strip() or None,
        )
        db.add(row)
        rows.append(WatchlistItem(stock_code=code, stock_name=row.stock_name))
    db.flush()
    return rows
