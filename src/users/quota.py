# -*- coding: utf-8 -*-
"""Phase 2 配额服务骨架。

针对 To C 模式下的「每日 N 次」类型配额, 提供:

- :func:`get_remaining` 查询某 (user, date, kind) 的当日剩余次数 (永远基于 UTC date)。
- :func:`try_consume`   原子地预扣配额, 成功返回 ``True``, 上限耗尽返回 ``False``。
- :func:`refund`        业务失败时回补一次配额。
- :func:`get_quota_snapshot` 一次拿出 analysis / agent 当日剩余, 用于前端顶栏渲染。

设计取舍:

- 按用户 + UTC date + kind 做行级 upsert, SQLite / PostgreSQL 均可。
- 当 BYOK 启用 (用户使用自带 Key) 时, 调用方传 ``bypass=True`` 跳过扣减; 配额仅约束「平台 Key」。
- :class:`QuotaConfig` 由调用方按用户 plan 计算后传入, 本模块不感知套餐。这样 Phase 2 接入 plan 表后无需改这里。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.storage import AppUserUsageCounter


# 业务侧使用的 kind 常量, 也是落库字段值
KIND_ANALYSIS = "analysis"
KIND_AGENT = "agent"
KIND_NOTIFY = "notify"

_VALID_KINDS = frozenset({KIND_ANALYSIS, KIND_AGENT, KIND_NOTIFY})


@dataclass(frozen=True)
class QuotaConfig:
    """当前用户在某 kind 上的每日上限快照。

    ``daily_limit <= 0`` 表示不限额 (例如管理员或 BYOK 走自己 Key)。
    """

    daily_limit: int


def _ensure_kind(kind: str) -> None:
    if kind not in _VALID_KINDS:
        raise ValueError(f"unsupported quota kind: {kind!r}")


def _today_utc() -> date:
    return datetime.utcnow().date()


def _get_counter(
    db: Session,
    *,
    user_id: int,
    counter_date: date,
    kind: str,
) -> Optional[AppUserUsageCounter]:
    stmt = select(AppUserUsageCounter).where(
        AppUserUsageCounter.user_id == user_id,
        AppUserUsageCounter.counter_date == counter_date,
        AppUserUsageCounter.kind == kind,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_used(
    db: Session,
    *,
    user_id: int,
    kind: str,
    on_date: Optional[date] = None,
) -> int:
    """返回该用户当天 kind 的已使用次数, 行不存在时按 0。"""
    _ensure_kind(kind)
    on_date = on_date or _today_utc()
    row = _get_counter(db, user_id=user_id, counter_date=on_date, kind=kind)
    return int(row.count) if row else 0


def get_remaining(
    db: Session,
    *,
    user_id: int,
    kind: str,
    config: QuotaConfig,
    on_date: Optional[date] = None,
) -> Optional[int]:
    """返回剩余配额, ``None`` 表示不限额。"""
    _ensure_kind(kind)
    if config.daily_limit <= 0:
        return None
    used = get_used(db, user_id=user_id, kind=kind, on_date=on_date)
    return max(0, config.daily_limit - used)


def try_consume(
    db: Session,
    *,
    user_id: int,
    kind: str,
    config: QuotaConfig,
    bypass: bool = False,
    on_date: Optional[date] = None,
) -> bool:
    """原子地扣减 1 次配额, 上限耗尽返回 ``False``。

    ``bypass=True``: BYOK / 管理员场景, 跳过扣减但仍返回 True, 调用方无需分支判断。
    ``config.daily_limit <= 0``: 视作不限额, 仍记录用量便于运营观察。
    """
    _ensure_kind(kind)
    if bypass:
        return True

    on_date = on_date or _today_utc()
    row = _get_counter(db, user_id=user_id, counter_date=on_date, kind=kind)
    used = int(row.count) if row else 0

    if config.daily_limit > 0 and used >= config.daily_limit:
        return False

    if row is None:
        row = AppUserUsageCounter(
            user_id=user_id,
            counter_date=on_date,
            kind=kind,
            count=1,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            # 并发场景下另一个事务可能已建行, 回退到 update 分支
            db.rollback()
            row = _get_counter(db, user_id=user_id, counter_date=on_date, kind=kind)
            if row is None:
                # 极端情况: 仍然查不到, 视为扣减失败让调用方重试
                return False
            if config.daily_limit > 0 and int(row.count) >= config.daily_limit:
                return False
            row.count = int(row.count) + 1
            db.add(row)
            db.flush()
    else:
        row.count = int(row.count) + 1
        db.add(row)
        db.flush()
    return True


def refund(
    db: Session,
    *,
    user_id: int,
    kind: str,
    on_date: Optional[date] = None,
) -> None:
    """业务失败时回补 1 次配额, 不会低于 0。"""
    _ensure_kind(kind)
    on_date = on_date or _today_utc()
    row = _get_counter(db, user_id=user_id, counter_date=on_date, kind=kind)
    if row is None or int(row.count) <= 0:
        return
    row.count = int(row.count) - 1
    db.add(row)
    db.flush()


@dataclass(frozen=True)
class QuotaSnapshot:
    """用于前端顶栏一次性渲染配额信息。"""

    analysis_used: int
    analysis_limit: int
    agent_used: int
    agent_limit: int

    @property
    def analysis_remaining(self) -> Optional[int]:
        if self.analysis_limit <= 0:
            return None
        return max(0, self.analysis_limit - self.analysis_used)

    @property
    def agent_remaining(self) -> Optional[int]:
        if self.agent_limit <= 0:
            return None
        return max(0, self.agent_limit - self.agent_used)


def get_quota_snapshot(
    db: Session,
    *,
    user_id: int,
    analysis_limit: int,
    agent_limit: int,
    on_date: Optional[date] = None,
) -> QuotaSnapshot:
    """一次性返回 analysis + agent 当日使用情况, 供 `/api/v1/account/status` 之类的端点使用。"""
    return QuotaSnapshot(
        analysis_used=get_used(db, user_id=user_id, kind=KIND_ANALYSIS, on_date=on_date),
        analysis_limit=analysis_limit,
        agent_used=get_used(db, user_id=user_id, kind=KIND_AGENT, on_date=on_date),
        agent_limit=agent_limit,
    )
