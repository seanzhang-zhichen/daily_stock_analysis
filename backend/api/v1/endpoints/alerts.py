# -*- coding: utf-8 -*-
"""告警 API 端点（Alert API endpoints）。

作为 ``AlertService`` 的薄 HTTP 适配层，对外提供告警规则的增删改查、触发历史与
通知记录的查询接口。所有数据均按当前登录用户（``current_user``）隔离；服务层抛出的
领域异常在这里统一转换为 ``ErrorResponse`` 结构返回给客户端。

依赖：
- 服务层：``src.services.alert_service.AlertService``
- Schema：``api.v1.schemas.alerts``
- 鉴权：``api.deps.get_current_user``
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_current_user
from src.storage import AppUser
from api.v1.schemas.alerts import (
    AlertDeleteResponse,
    AlertNotificationListResponse,
    AlertRuleCreateRequest,
    AlertRuleItem,
    AlertRuleListResponse,
    AlertRuleTestResponse,
    AlertRuleUpdateRequest,
    AlertTriggerListResponse,
)
from api.v1.schemas.common import ErrorResponse
from src.services.alert_service import (
    AlertNotFoundError,
    AlertService,
    AlertServiceError,
    UnsupportedAlertTypeError,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _bad_request(exc: Exception, *, error: str = "validation_error") -> HTTPException:
    """将校验或服务层领域异常映射为 HTTP 400 响应。"""
    return HTTPException(
        status_code=400,
        detail={"error": error, "message": str(exc)},
    )


def _not_found(exc: Exception) -> HTTPException:
    """将“资源不存在”类异常映射为 HTTP 404 响应。"""
    return HTTPException(
        status_code=404,
        detail={"error": "not_found", "message": str(exc)},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    """记录告警相关的未预期异常并映射为 HTTP 500 响应。

    在抛出前会先 ``logger.error`` 记录完整堆栈，便于事后排查。
    """
    logger.error("%s: %s", message, exc, exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: {str(exc)}"},
    )


@router.post(
    "/rules",
    response_model=AlertRuleItem,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Create alert rule",
)
def create_rule(
    request: AlertRuleCreateRequest,
    current_user: AppUser = Depends(get_current_user),
) -> AlertRuleItem:
    """为当前登录用户创建一条告警规则。"""
    service = AlertService()
    try:
        return AlertRuleItem(**service.create_rule(
            request.model_dump(),
            user_id=current_user.id,
        ))
    except UnsupportedAlertTypeError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except AlertServiceError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except Exception as exc:
        raise _internal_error("Create alert rule failed", exc)


@router.get(
    "/rules",
    response_model=AlertRuleListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List alert rules",
)
def list_rules(
    enabled: Optional[bool] = Query(None, description="Optional enabled filter"),
    alert_type: Optional[str] = Query(None, description="Optional alert type filter"),
    target_scope: Optional[str] = Query(None, description="Optional target scope filter"),
    target: Optional[str] = Query(None, description="Optional target filter"),
    source: Optional[str] = Query(None, description="Optional source filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AppUser = Depends(get_current_user),
) -> AlertRuleListResponse:
    """分页查询告警规则，支持按启用状态、类型、作用范围等过滤。"""
    service = AlertService()
    try:
        return AlertRuleListResponse(
            **service.list_rules(
                enabled=enabled,
                alert_type=alert_type,
                target_scope=target_scope,
                target=target,
                source=source,
                user_id=current_user.id,
                page=page,
                page_size=page_size,
            )
        )
    except Exception as exc:
        raise _internal_error("List alert rules failed", exc)


@router.get(
    "/rules/{rule_id}",
    response_model=AlertRuleItem,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get alert rule",
)
def get_rule(
    rule_id: int,
    current_user: AppUser = Depends(get_current_user),
) -> AlertRuleItem:
    """获取属于当前用户的单条告警规则。"""
    service = AlertService()
    try:
        return AlertRuleItem(**service.get_rule(
            rule_id,
            user_id=current_user.id,
        ))
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Get alert rule failed", exc)


@router.patch(
    "/rules/{rule_id}",
    response_model=AlertRuleItem,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Update alert rule",
)
def update_rule(
    rule_id: int,
    request: AlertRuleUpdateRequest,
    current_user: AppUser = Depends(get_current_user),
) -> AlertRuleItem:
    """局部更新现有告警规则的可变字段。"""
    service = AlertService()
    try:
        # 仅提交请求体中显式给出的字段，未提供的字段保持原值
        payload = request.model_dump(exclude_unset=True)
        return AlertRuleItem(**service.update_rule(
            rule_id,
            payload,
            user_id=current_user.id,
        ))
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except UnsupportedAlertTypeError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except AlertServiceError as exc:
        raise _bad_request(exc, error=exc.error_code)
    except Exception as exc:
        raise _internal_error("Update alert rule failed", exc)


@router.delete(
    "/rules/{rule_id}",
    response_model=AlertDeleteResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Delete alert rule",
)
def delete_rule(
    rule_id: int,
    current_user: AppUser = Depends(get_current_user),
) -> AlertDeleteResponse:
    """删除单条告警规则，并返回删除数量。"""
    service = AlertService()
    try:
        if not service.delete_rule(rule_id, user_id=current_user.id):
            raise AlertNotFoundError(f"Alert rule not found: {rule_id}")
        return AlertDeleteResponse(deleted=1)
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Delete alert rule failed", exc)


@router.post(
    "/rules/{rule_id}/enable",
    response_model=AlertRuleItem,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Enable alert rule",
)
def enable_rule(
    rule_id: int,
    current_user: AppUser = Depends(get_current_user),
) -> AlertRuleItem:
    """启用一条告警规则，不修改其它字段。"""
    service = AlertService()
    try:
        return AlertRuleItem(**service.enable_rule(
            rule_id, True,
            user_id=current_user.id,
        ))
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Enable alert rule failed", exc)


@router.post(
    "/rules/{rule_id}/disable",
    response_model=AlertRuleItem,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Disable alert rule",
)
def disable_rule(
    rule_id: int,
    current_user: AppUser = Depends(get_current_user),
) -> AlertRuleItem:
    """禁用一条告警规则，但保留其历史触发记录。"""
    service = AlertService()
    try:
        return AlertRuleItem(**service.enable_rule(
            rule_id, False,
            user_id=current_user.id,
        ))
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Disable alert rule failed", exc)


@router.post(
    "/rules/{rule_id}/test",
    response_model=AlertRuleTestResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Dry-run alert rule",
)
def test_rule(
    rule_id: int,
    current_user: AppUser = Depends(get_current_user),
) -> AlertRuleTestResponse:
    """用当前行情数据对一条告警规则进行试跑（dry-run），不真正触发通知。"""
    service = AlertService()
    try:
        return AlertRuleTestResponse(**service.test_rule(
            rule_id,
            user_id=current_user.id,
        ))
    except AlertNotFoundError as exc:
        raise _not_found(exc)
    except Exception as exc:
        raise _internal_error("Test alert rule failed", exc)


@router.get(
    "/triggers",
    response_model=AlertTriggerListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List alert trigger history",
)
def list_triggers(
    rule_id: Optional[int] = Query(None, description="Optional rule id filter"),
    target: Optional[str] = Query(None, description="Optional target filter"),
    status: Optional[str] = Query(None, description="Optional status filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AppUser = Depends(get_current_user),
) -> AlertTriggerListResponse:
    """查询当前用户的告警触发历史事件。"""
    service = AlertService()
    try:
        return AlertTriggerListResponse(
            **service.list_triggers(
                rule_id=rule_id,
                target=target,
                status=status,
                user_id=current_user.id,
                page=page,
                page_size=page_size,
            )
        )
    except Exception as exc:
        raise _internal_error("List alert triggers failed", exc)


@router.get(
    "/notifications",
    response_model=AlertNotificationListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List alert notification attempts",
)
def list_notifications(
    trigger_id: Optional[int] = Query(None, description="Optional trigger id filter"),
    channel: Optional[str] = Query(None, description="Optional channel filter"),
    success: Optional[bool] = Query(None, description="Optional success filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AppUser = Depends(get_current_user),
) -> AlertNotificationListResponse:
    """查询由告警触发产生的通知发送记录。"""
    service = AlertService()
    try:
        return AlertNotificationListResponse(
            **service.list_notifications(
                trigger_id=trigger_id,
                channel=channel,
                success=success,
                user_id=current_user.id,
                page=page,
                page_size=page_size,
            )
        )
    except Exception as exc:
        raise _internal_error("List alert notifications failed", exc)
