# -*- coding: utf-8 -*-
"""Phase 2 配额守卫 (quota guard)。

把「解析当前用户套餐 + 尝试扣减一次配额」封装成业务侧可一行调用的 helper,
让 ``analysis`` / ``agent`` 等 endpoint 不需要自己感知 plan 表 / settings / try_consume 细节。

使用模式::

    user = get_current_user(request)
    outcome = enforce_quota(db, user=user, kind=KIND_ANALYSIS)
    if outcome.exceeded:
        raise QuotaExceeded(outcome)
    # ... 业务执行 ...
    # 失败时:
    #     refund_quota(db, user=user, kind=KIND_ANALYSIS, on_date=outcome.on_date)

设计取舍:

- ``user is None`` 属于调用方错误, 业务接口必须先完成多用户登录鉴权。
- ``daily_limit <= 0`` 视作不限额, 仍然记录用量便于后续运营观察 (与 ``try_consume`` 保持一致)。
- 不抛 :class:`HTTPException`, 由 endpoint 层根据自己的协议格式化错误响应,
  方便复用到 SSE / 后台任务等不同响应形态。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from src.storage import AppUser
from src.users.plans import ResolvedPlan, resolve_user_plan
from src.users.quota import (
    KIND_AGENT,
    KIND_ANALYSIS,
    KIND_NOTIFY,
    QuotaConfig,
    get_used as _get_used,
    refund as _refund,
    try_consume as _try_consume,
)


def _today_utc() -> date:
    return datetime.utcnow().date()


_KIND_TO_LIMIT_FIELD = {
    KIND_ANALYSIS: "daily_analysis_limit",
    KIND_AGENT: "daily_agent_limit",
    # notify 暂未在 plan 表上独立配置, 默认跟随 agent 限额
    KIND_NOTIFY: "daily_agent_limit",
}


def _plan_limit_for(plan: ResolvedPlan, kind: str) -> int:
    field = _KIND_TO_LIMIT_FIELD.get(kind)
    if field is None:
        raise ValueError(f"unsupported quota kind: {kind!r}")
    return int(getattr(plan, field, 0))


@dataclass(frozen=True)
class QuotaOutcome:
    """配额检查结果, 调用方只需关心 :attr:`exceeded`。

    - :attr:`consumed` 为 ``True`` 表示已成功记一次用量, 业务失败时调用方应
      使用 :func:`refund_quota` 把它退回去 (避免错误占用配额)。
    - :attr:`bypassed` 为 ``True`` 表示由于不限额等原因没有真正落库, 业务无需 refund。
    - :attr:`limit` / :attr:`used` / :attr:`remaining` 来自 plan + 当日用量,
      可以直接喂给前端做提示。
    """

    user: AppUser
    plan: Optional[ResolvedPlan]
    kind: str
    on_date: date
    limit: int
    used: int
    consumed: bool
    bypassed: bool
    exceeded: bool

    @property
    def remaining(self) -> Optional[int]:
        if self.limit <= 0:
            return None
        return max(0, self.limit - self.used)


def enforce_quota(
    db: Session,
    *,
    user: AppUser,
    kind: str,
    on_date: Optional[date] = None,
) -> QuotaOutcome:
    """统一的「读 plan + 尝试扣 1 次配额」入口。

    返回值 :class:`QuotaOutcome` 描述本次结果, 调用方据此决定:

    - ``exceeded=True``: 上限耗尽, 应当返回 402 ``quota_exceeded``;
    - ``consumed=True`` 但业务后续失败: 调用 :func:`refund_quota` 退回;
    - ``bypassed=True``: 没有真正扣减, 业务无需关心。
    """

    target_date = on_date or _today_utc()

    if user is None:
        raise ValueError("quota enforcement requires an authenticated user")

    plan = resolve_user_plan(db, user)
    daily_limit = _plan_limit_for(plan, kind)

    config = QuotaConfig(daily_limit=daily_limit)
    consumed = _try_consume(
        db,
        user_id=user.id,
        kind=kind,
        config=config,
        bypass=False,
        on_date=target_date,
    )

    # 重新读一次 used, 让前端 / 日志能看到准确剩余值
    used = _get_used(db, user_id=user.id, kind=kind, on_date=target_date)

    if not consumed:
        return QuotaOutcome(
            user=user,
            plan=plan,
            kind=kind,
            on_date=target_date,
            limit=daily_limit,
            used=used,
            consumed=False,
            bypassed=False,
            exceeded=True,
        )

    return QuotaOutcome(
        user=user,
        plan=plan,
        kind=kind,
        on_date=target_date,
        limit=daily_limit,
        used=used,
        consumed=True,
        bypassed=False,
        exceeded=False,
    )


def refund_quota(
    db: Session,
    *,
    user: AppUser,
    kind: str,
    on_date: Optional[date] = None,
) -> None:
    """业务失败时回补一次配额; kind 不合法时静默忽略。"""

    try:
        _refund(db, user_id=user.id, kind=kind, on_date=on_date)
    except ValueError:
        # 不让 refund 阶段把业务错误吞掉
        return


def quota_exceeded_payload(outcome: QuotaOutcome) -> dict:
    """把超额结果格式化成前端约定的 ``quota_exceeded`` 错误体。

    与 ``frontend/web/src/api/error.ts`` 中超额引导对话框约定保持一致:
    顶层包含 ``error`` / ``message`` / ``kind`` / ``limit`` / ``used`` / ``remaining`` /
    ``planCode`` 等字段, 便于前端不解析 ``detail`` 即可渲染。
    """

    plan_code = outcome.plan.code if outcome.plan is not None else "free"
    plan_name = outcome.plan.name if outcome.plan is not None else "Free"
    return {
        "error": "quota_exceeded",
        "message": f"今日 {outcome.kind} 额度已用完 ({outcome.used}/{outcome.limit})",
        "kind": outcome.kind,
        "limit": outcome.limit,
        "used": outcome.used,
        "remaining": outcome.remaining if outcome.remaining is not None else 0,
        "planCode": plan_code,
        "planName": plan_name,
    }


__all__ = [
    "QuotaOutcome",
    "enforce_quota",
    "refund_quota",
    "quota_exceeded_payload",
]
