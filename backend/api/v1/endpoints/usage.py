# -*- coding: utf-8 -*-
"""LLM usage summary and growth-event endpoints.

用量汇总接口读取后端记录的 LLM 调用统计；增长埋点接口接收前端关键事件并尽力写库。
埋点写入失败只记录 warning，不影响用户主流程。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_database_manager, get_db
from api.v1.schemas.usage import UsageSummaryResponse
from src.storage import DatabaseManager, AppGrowthEvent
from src.users.config import SESSION_COOKIE_NAME, load_user_mode_settings
from src.users.sessions import resolve_session
from src.auth import get_client_ip

logger = logging.getLogger(__name__)

# Usage periods are product-facing Beijing-calendar windows, not UTC windows.
_CST = timezone(timedelta(hours=8))

router = APIRouter()

_VALID_PERIODS = {"today", "month", "all"}

# 限制允许上报的事件名，防止任意前端/第三方请求污染埋点表。
_ALLOWED_EVENTS = {
    "page.view",
    "user.register",
    "user.first_analysis",
    "user.upgrade_click",
    "user.upgrade_success",
    "user.daily_push_enable",
    "quota.exceeded",
    "payment.initiated",
    "payment.success",
    "onboarding.complete",
}


def _date_range(period: str):
    """Return naive Beijing-local datetime bounds for the requested period.

    存储层当前按 naive datetime 查询，因此这里先按 UTC+8 计算业务窗口，再移除
    tzinfo，保证“今天/月初”与产品展示口径一致。
    """
    now = datetime.now(tz=_CST).replace(tzinfo=None)  # naive, Beijing local
    if period == "today":
        from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        from_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:  # all
        from_dt = datetime(2000, 1, 1)
    return from_dt, now


@router.get(
    "/summary",
    response_model=UsageSummaryResponse,
    summary="LLM token usage summary",
    description="Aggregate token consumption by period, call type, and model.",
)
def get_usage_summary(
    period: str = Query("month", description="'today' | 'month' | 'all'"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> UsageSummaryResponse:
    """Return aggregated LLM usage for today, current month, or all time."""
    if period not in _VALID_PERIODS:
        period = "month"

    from_dt, to_dt = _date_range(period)

    data = db_manager.get_llm_usage_summary(from_dt, to_dt)

    return UsageSummaryResponse(
        period=period,
        from_date=from_dt.date().isoformat(),
        to_date=to_dt.date().isoformat(),
        total_calls=data["total_calls"],
        total_tokens=data["total_tokens"],
        by_call_type=data["by_call_type"],
        by_model=data["by_model"],
    )


# ── 增长埋点 (Phase 6) ────────────────────────────────────────────────────────


class GrowthEventRequest(BaseModel):
    """Frontend growth-event payload.

    ``sessionId`` 使用别名兼容前端 camelCase；未登录用户也可以上报匿名事件。
    """

    event: str = Field(..., max_length=64, description="事件名（见 _ALLOWED_EVENTS）")
    props: Optional[Dict[str, Any]] = Field(default=None, description="附加属性 JSON（可选）")
    session_id: Optional[str] = Field(default=None, alias="sessionId", max_length=128)


@router.post(
    "/events",
    summary="上报增长埋点事件",
    description=(
        "前端在关键转化节点调用此接口上报事件。"
        "无需登录（登录态时自动关联 user_id）。"
        "未知事件名返回 204 静默忽略，不写库。"
    ),
    status_code=204,
    response_class=Response,
    response_model=None,
)
async def record_growth_event(
    body: GrowthEventRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    """Record a whitelisted growth event and silently ignore invalid events."""
    # 静默忽略白名单外的事件，保持上报端简单，不让埋点错误打扰用户体验。
    if body.event not in _ALLOWED_EVENTS:
        return

    # 尽量解析 user_id；匿名、过期 cookie 或 DB 查询异常都不阻断埋点写入。
    user_id: Optional[int] = None
    try:
        cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
        if cookie_value:
            u = resolve_session(db, cookie_value)
            if u is not None:
                user_id = int(u.id)
    except Exception:  # noqa: BLE001
        pass

    props_json: Optional[str] = None
    if body.props:
        try:
            # props 存为 JSON 字符串，避免埋点表 schema 随事件属性频繁变化。
            props_json = json.dumps(body.props, ensure_ascii=False)
        except (TypeError, ValueError):
            props_json = None

    ev = AppGrowthEvent(
        user_id=user_id,
        session_id=body.session_id,
        event=body.event,
        props=props_json,
        ip=get_client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:512],
    )
    try:
        db.add(ev)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("failed to record growth event %r", body.event, exc_info=True)
