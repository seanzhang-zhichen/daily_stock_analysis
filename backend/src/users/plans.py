# -*- coding: utf-8 -*-
"""套餐 / 订阅 / 兑换码服务 (Phase 2)。

设计取舍:

- 套餐权益以 ``app_plans`` 为唯一运行期配置源。
- ``redeem_code(user, code)`` 会写一条 :class:`AppSubscription`, 并更新
  ``AppUser.plan_code`` / ``plan_expires_at``, 同时把兑换码标记为已用。
- 不直接接支付通道; ``source='paid'`` 仍可被外部脚本写入。

调用顺序示例 (endpoint 内):
    plan = resolve_user_plan(db, user)
    quota_cfg = QuotaConfig(daily_limit=plan.daily_analysis_limit)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from src.storage import AppPlan, AppRedeemCode, AppSubscription, AppUser
from src.users.errors import UserError, UserErrorCode


@dataclass(frozen=True)
class ResolvedPlan:
    """已解析的用户套餐快照, 供配额服务消费。"""

    code: str
    name: str
    daily_analysis_limit: int
    daily_agent_limit: int
    max_stocks: int
    allowed_models: List[str]
    can_webhook: bool
    expires_at: Optional[datetime]

    @property
    def is_pro(self) -> bool:
        return self.code != "free"


_FREE_PLAN_CODE = "free"


def _parse_allowed_models(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = [item.strip() for item in str(raw).split(",")]
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _resolved_plan_from_row(row: AppPlan, *, expires_at: Optional[datetime]) -> ResolvedPlan:
    code = (row.code or "").strip().lower()
    return ResolvedPlan(
        code=code,
        name=row.name,
        daily_analysis_limit=int(row.daily_analysis_limit),
        daily_agent_limit=int(row.daily_agent_limit),
        max_stocks=int(row.max_stocks),
        allowed_models=_parse_allowed_models(row.allowed_models),
        can_webhook=False if code == _FREE_PLAN_CODE else bool(row.can_webhook),
        expires_at=expires_at,
    )


def serialize_plan_row(row: AppPlan, *, include_allowed_models: bool = False, source: str = "db") -> dict:
    code = (row.code or "").strip().lower()
    payload = {
        "code": code,
        "name": row.name,
        "dailyAnalysisLimit": int(row.daily_analysis_limit),
        "dailyAgentLimit": int(row.daily_agent_limit),
        "maxStocks": int(row.max_stocks),
        "canWebhook": False if code == _FREE_PLAN_CODE else bool(row.can_webhook),
        "priceCents": 0 if code == _FREE_PLAN_CODE else int(row.price_cents),
        "currency": row.currency or "CNY",
        "isActive": True if code == _FREE_PLAN_CODE else bool(row.is_active),
        "source": source,
        "isPersisted": source == "db",
    }
    if include_allowed_models:
        payload["allowedModels"] = _parse_allowed_models(row.allowed_models)
    return payload


def _sort_plan_payloads(plans: List[dict]) -> List[dict]:
    order = {"free": 0, "pro": 1, "pro_yearly": 2}
    return sorted(
        plans,
        key=lambda p: (
            order.get(str(p.get("code") or ""), 100),
            int(p.get("priceCents") or 0),
            str(p.get("code") or ""),
        ),
    )


def list_plan_catalog(
    db: Session,
    *,
    include_inactive: bool = False,
    include_allowed_models: bool = False,
) -> List[dict]:
    plans_by_code = {}
    rows = db.query(AppPlan).order_by(AppPlan.price_cents.asc(), AppPlan.code.asc()).all()
    for row in rows:
        code = (row.code or "").strip().lower()
        if code == _FREE_PLAN_CODE or bool(row.is_active) or include_inactive:
            plans_by_code[code] = serialize_plan_row(
                row,
                include_allowed_models=include_allowed_models,
                source="db",
            )
        else:
            plans_by_code.pop(code, None)
    return _sort_plan_payloads(
        [p for p in plans_by_code.values() if include_inactive or bool(p.get("isActive", True))]
    )


def _free_plan_from_db(db: Session) -> ResolvedPlan:
    row = db.query(AppPlan).filter(AppPlan.code == _FREE_PLAN_CODE).first()
    if row is None:
        raise UserError(UserErrorCode.NOT_FOUND, "app_plans 缺少 free 套餐配置")
    return _resolved_plan_from_row(row, expires_at=None)


def get_plan_by_code(db: Session, plan_code: str) -> Optional[AppPlan]:
    return (
        db.query(AppPlan)
        .filter(AppPlan.code == plan_code, AppPlan.is_active.is_(True))
        .first()
    )


def resolve_user_plan(
    db: Session,
    user: AppUser,
) -> ResolvedPlan:
    """根据用户的 ``plan_code`` + ``plan_expires_at`` 解析当前生效套餐。

    - 过期后自动回退到 free 套餐 (但不会持久化, 由调用方决定何时同步)。
    - ``AppPlan`` 表里查不到对应行时, 回退到 free 档。
    """
    now = datetime.utcnow()

    code = (user.plan_code or _FREE_PLAN_CODE).strip().lower()
    expired = user.plan_expires_at is not None and user.plan_expires_at < now
    if expired or code == _FREE_PLAN_CODE:
        return _free_plan_from_db(db)

    row = get_plan_by_code(db, code)
    if row is None:
        return _free_plan_from_db(db)

    return _resolved_plan_from_row(row, expires_at=user.plan_expires_at)


def grant_plan(
    db: Session,
    user: AppUser,
    *,
    plan_code: str,
    grant_days: int,
    source: str = "manual",
    note: Optional[str] = None,
) -> AppSubscription:
    """给用户开通 / 续期某档套餐, 写一条 ``AppSubscription`` 并更新 ``AppUser``。"""
    plan_code_norm = (plan_code or "").strip().lower() or _FREE_PLAN_CODE
    if plan_code_norm == _FREE_PLAN_CODE:
        raise UserError(UserErrorCode.INVITE_CODE_INVALID, "free 套餐无需手动开通")
    if grant_days <= 0:
        raise UserError(UserErrorCode.INVITE_CODE_INVALID, "grant_days 必须为正数")

    plan_row = get_plan_by_code(db, plan_code_norm)
    if plan_row is None:
        raise UserError(UserErrorCode.INVITE_CODE_INVALID, f"未知套餐: {plan_code_norm}")

    now = datetime.utcnow()
    # 续期语义: 若当前套餐尚未过期, 在原过期时间上累加; 否则从当下开始计算
    base = user.plan_expires_at if (user.plan_expires_at and user.plan_expires_at > now) else now
    new_expires = base + timedelta(days=int(grant_days))

    sub = AppSubscription(
        user_id=user.id,
        plan_code=plan_code_norm,
        source=source,
        started_at=now,
        expires_at=new_expires,
        note=(note or "")[:255] or None,
    )
    db.add(sub)

    user.plan_code = plan_code_norm
    user.plan_expires_at = new_expires
    db.add(user)
    db.flush()
    return sub


def redeem_code(
    db: Session,
    user: AppUser,
    *,
    code: str,
) -> AppSubscription:
    """用兑换码升级套餐。兑换码消费成功后被标记为 ``redeemed_by`` + ``redeemed_at``。"""
    code_norm = (code or "").strip()
    if not code_norm:
        raise UserError(UserErrorCode.INVITE_CODE_REQUIRED, "请输入兑换码")

    row = db.query(AppRedeemCode).filter(AppRedeemCode.code == code_norm).first()
    if row is None:
        raise UserError(UserErrorCode.INVITE_CODE_INVALID, "兑换码无效")
    if row.redeemed_by is not None:
        raise UserError(UserErrorCode.INVITE_CODE_INVALID, "兑换码已被使用")
    now = datetime.utcnow()
    if row.expires_at is not None and row.expires_at < now:
        raise UserError(UserErrorCode.TOKEN_EXPIRED, "兑换码已过期")

    sub = grant_plan(
        db,
        user,
        plan_code=row.plan_code,
        grant_days=int(row.grant_days),
        source="redeem",
        note=f"redeem:{code_norm}",
    )
    row.redeemed_by = user.id
    row.redeemed_at = now
    db.add(row)
    db.flush()
    return sub
