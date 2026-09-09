# -*- coding: utf-8 -*-
"""Stock analysis and market-review endpoints.

本模块覆盖单股/批量分析触发、同步分析响应、异步任务受理、任务状态查询、SSE 任务流
以及大盘复盘后台任务。endpoint 层负责输入归一化、配额/积分扣减与失败返还、重复任务
映射、报告 schema 组装和用户隔离；实际分析流程由任务队列、AnalyzerService 与核心复盘
模块执行。
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Query, Body
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from api.deps import get_config_dep, get_current_user, get_db
from src.storage import AppUser
from src.users import (
    KIND_ANALYSIS,
    enforce_quota,
    quota_exceeded_payload,
    refund_quota,
)
from src.users.credits import (
    CreditOutcome,
    credit_exceeded_payload,
    enforce_credits,
    refund_consumed_credits,
)
from api.v1.schemas.analysis import (
    AnalyzeRequest,
    AnalysisResultResponse,
    TaskAccepted,
    BatchTaskAcceptedResponse,
    BatchTaskAcceptedItem,
    BatchDuplicateTaskItem,
    TaskStatus,
    TaskInfo,
    TaskListResponse,
    DuplicateTaskErrorResponse,
    MarketReviewRequest,
    MarketReviewAccepted,
)
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.history import (
    AnalysisReport,
    ReportMeta,
    ReportSummary,
    ReportStrategy,
    ReportDetails,
)
from data_provider.base import canonical_stock_code, normalize_stock_code
from src.config import Config
from src.core.market_review_lock import (
    MarketReviewExecutionLock as _MarketReviewExecutionLock,
    market_review_lock_path,
    release_market_review_lock as _release_market_review_lock,
    try_acquire_market_review_lock as _try_acquire_market_review_lock,
)
from src.core.market_review_runtime import (
    build_market_review_runtime as _runtime_build_market_review_runtime,
)
from src.report_language import get_localized_stock_name, normalize_report_language
from src.services.name_to_code_resolver import resolve_name_to_code
from src.services.empty_news import empty_news_disclosure_from_stored
from src.services.stock_code_utils import is_code_like
from src.services.task_queue import (
    get_task_queue,
    DuplicateTaskError,
    TaskStatus as TaskStatusEnum,
)
from src.utils.data_processing import (
    normalize_model_used,
    parse_json_field,
    extract_fundamental_detail_fields,
    extract_board_detail_fields,
    extract_market_structure_context,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_SUPPORTED_FREE_TEXT_RE = re.compile(r"^[A-Za-z0-9.*\-+\u3400-\u9fff\s]+$")


def _current_user_id_or_none(current_user: Any) -> Optional[int]:
    """从 AppUser 之类的对象中提取数值型 user id。"""
    user_id = getattr(current_user, "id", None)
    if user_id is None:
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def _db_user(db: Session, current_user: AppUser) -> AppUser:
    """在配额/积分扣减前尽量从 DB 重新加载当前用户，保证状态最新。"""
    user_id = _current_user_id_or_none(current_user)
    if user_id is None or not hasattr(db, "query"):
        return current_user
    try:
        row = db.query(AppUser).filter(AppUser.id == user_id).first()
    except Exception:  # noqa: BLE001
        # DB 查询失败时回退到请求上下文中的 user 对象，避免阻塞请求
        return current_user
    return row if isinstance(row, AppUser) else current_user


def _market_review_lock_path(config: Config) -> Path:
    """返回用于串行化大盘复盘任务的本地文件系统锁路径。"""
    return market_review_lock_path(config)


def _compute_market_review_override_region(config: Config) -> Optional[str]:
    """按交易日历过滤大盘复盘区域，非交易日时返回空串以提示跳过。"""
    if not getattr(config, "trading_day_check_enabled", True):
        return None

    try:
        from src.core.trading_calendar import (
            get_open_markets_today,
            compute_effective_region,
        )

        open_markets = get_open_markets_today()
        return compute_effective_region(
            getattr(config, "market_review_region", "cn") or "cn",
            open_markets,
        )
    except Exception as exc:
        # 交易日历服务不可用时，按配置继续执行大盘复盘
        logger.warning("大盘复盘交易日过滤失败，按配置继续执行: %s", exc)
        return None


def _build_market_review_runtime(config: Config, source_message: Optional[Any] = None) -> tuple[Any, Any, Any]:
    """构造大盘复盘所需的通知器、分析器、检索服务等运行时依赖。"""
    return _runtime_build_market_review_runtime(config, source_message)


def _run_market_review_background(
    send_notification: bool,
    override_region: Optional[str] = None,
    lock_token: Optional[_MarketReviewExecutionLock] = None,
    config: Optional[Config] = None,
    query_id: Optional[str] = None,
) -> None:
    """在 API 响应已受理后再真正执行大盘复盘任务。"""
    from src.core.market_review import run_market_review

    runtime_config = config or get_config_dep()
    try:
        notifier, analyzer, search_service = _build_market_review_runtime(runtime_config)
        review_kwargs = {
            "notifier": notifier,
            "analyzer": analyzer,
            "search_service": search_service,
            "send_notification": send_notification,
            "override_region": override_region,
        }
        if query_id:
            review_kwargs["query_id"] = query_id
        report = run_market_review(**review_kwargs)
        if not report:
            raise RuntimeError("大盘复盘未返回可持久化报告")
        return {"result": report}
    finally:
        # 无论成功失败，都要释放本地锁，避免长期占用阻塞下一次提交
        _release_market_review_lock(lock_token)


def _invalid_analysis_input_error() -> HTTPException:
    """针对不合法自由文本输入返回统一的 400 响应。"""
    return HTTPException(
        status_code=400,
        detail={
            "error": "validation_error",
            "message": "请输入有效的股票代码或股票名称",
        },
    )


def _is_obviously_invalid_analysis_input(text: str) -> bool:
    """早期拒绝明显是乱码或不支持字符的输入，减少后续昂贵的解析开销。"""
    if not text or is_code_like(text):
        return False

    if not _SUPPORTED_FREE_TEXT_RE.fullmatch(text):
        return True

    # 形如 "abc123" 的字母数字混合通常是 OCR 噪声，直接拒绝避免进入名称解析
    has_letters = any(ch.isalpha() and ch.isascii() for ch in text)
    has_digits = any(ch.isdigit() for ch in text)
    return has_letters and has_digits


def _resolve_and_normalize_input(raw_value: str) -> str:
    """解析并归一化分析请求的股票输入。

    - 类代码输入：走原有的规范化路径。
    - 非代码输入：必须能解析为已知股票代码。
    - 明显的无效输入会在进入昂贵的解析/任务队列前被拒绝。
    """
    text = (raw_value or "").strip()
    if not text:
        return ""

    # 优先识别已注册的指数身份（包括 CSI canonical id），
    # 避免被旧版股票代码规范化器丢失交易所信息
    try:
        from src.services.stock_list_parser import ParseStatus, parse_analysis_target
        target = parse_analysis_target(text)
        if target.status == ParseStatus.INDEX:
            return target.canonical_id
        if target.status == ParseStatus.UNSUPPORTED:
            raise _invalid_analysis_input_error()
    except ImportError:
        pass

    if is_code_like(text):
        return canonical_stock_code(text)

    if _is_obviously_invalid_analysis_input(text):
        raise _invalid_analysis_input_error()

    resolved = resolve_name_to_code(text)
    if resolved:
        return canonical_stock_code(resolved)

    raise _invalid_analysis_input_error()


# ============================================================
# POST /analyze - 触发股票分析
# ============================================================

@router.post(
    "/analyze",
    response_model=AnalysisResultResponse,
    responses={
        200: {"description": "分析完成（同步模式）", "model": AnalysisResultResponse},
        202: {
            "description": "分析任务已接受（异步模式）",
            "model": Union[TaskAccepted, BatchTaskAcceptedResponse],
        },
        400: {"description": "请求参数错误", "model": ErrorResponse},
        402: {"description": "今日 AI 分析额度已用完", "model": ErrorResponse},
        409: {"description": "股票正在分析中，拒绝重复提交", "model": DuplicateTaskErrorResponse},
        500: {"description": "分析失败", "model": ErrorResponse},
    },
    summary="触发股票分析",
    description="启动 AI 智能分析任务，支持同步和异步模式。异步模式下相同股票代码不允许重复提交。"
)
def trigger_analysis(
        request: AnalyzeRequest,
        config: Config = Depends(get_config_dep),
        db: Session = Depends(get_db),
        current_user: AppUser = Depends(get_current_user),
) -> Union[AnalysisResultResponse, JSONResponse]:
    """在输入/配额归一化后，触发同步或异步的股票分析任务。"""
    current_user_id = _current_user_id_or_none(current_user)
    if current_user_id is not None:
        current_user = _db_user(db, current_user)
        current_user_id = _current_user_id_or_none(current_user)
    stock_codes = []
    if request.stock_code:
        stock_codes.append(request.stock_code)
    if request.stock_codes:
        stock_codes.extend(request.stock_codes)

    if not stock_codes:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_error",
                "message": "必须提供 stock_code 或 stock_codes 参数"
            }
        )

    # 归一化并去重输入，保留向后兼容的输入顺序
    resolved = [_resolve_and_normalize_input(c) for c in stock_codes]

    seen = set()
    unique_codes = []
    for code in resolved:
        if not code:
            continue
        # 使用 normalize_stock_code 把 "600519" 与 "600519.SH" 合并为同一标的
        norm = normalize_stock_code(code)
        if norm not in seen:
            seen.add(norm)
            unique_codes.append(code)

    stock_codes = unique_codes

    # 限制单次请求的股票数量，避免被滥用造成服务端 DoS
    MAX_BATCH_SIZE = 50
    if len(stock_codes) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_error",
                "message": f"单次分析请求最多支持 {MAX_BATCH_SIZE} 只股票"
            }
        )

    if not stock_codes:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_error",
                "message": "股票代码不能为空或仅包含空白字符"
            }
        )

    # 同步模式仅支持单只股票，批量请走异步模式
    if not request.async_mode:
        if len(stock_codes) > 1:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "validation_error",
                    "message": "同步模式仅支持单只股票分析，请使用 async_mode=true 进行批量分析"
                }
            )
        outcome = None
        credit_outcome: Optional[CreditOutcome] = None
        if current_user_id is not None:
            outcome = enforce_quota(db, user=current_user, kind=KIND_ANALYSIS)
            if outcome.exceeded:
                db.commit()
                return JSONResponse(status_code=402, content=quota_exceeded_payload(outcome))
            credit_outcome = enforce_credits(
                db,
                user=current_user,
                kind=KIND_ANALYSIS,
                related_type="analysis",
                related_id=stock_codes[0],
            )
            if credit_outcome.exceeded:
                # 积分不足时同步回滚本次分析已扣的日额度，保持一致
                if outcome.consumed:
                    refund_quota(db, user=current_user, kind=KIND_ANALYSIS, on_date=outcome.on_date)
                db.commit()
                return JSONResponse(status_code=402, content=credit_exceeded_payload(credit_outcome))
            if outcome.consumed or (credit_outcome and credit_outcome.consumed):
                db.commit()
        try:
            return _handle_sync_analysis(
                stock_codes[0],
                request,
                user_id=current_user_id,
            )
        except Exception:
            # 同步分析失败：把已扣的积分与日额度返还给用户
            if credit_outcome and credit_outcome.consumed:
                refund_consumed_credits(
                    db,
                    user=current_user,
                    outcome=credit_outcome,
                    related_type="analysis",
                    related_id=stock_codes[0],
                )
            if outcome and outcome.consumed:
                refund_quota(db, user=current_user, kind=KIND_ANALYSIS, on_date=outcome.on_date)
            if (outcome and outcome.consumed) or (credit_outcome and credit_outcome.consumed):
                db.commit()
            raise

    # 异步批量: 按提交的股票数扣减 N 次, 任一未通过即整批拒绝
    consumed_count = 0
    consumed_on_date = None
    consumed_credit_outcomes: list[CreditOutcome] = []
    if current_user_id is not None and len(stock_codes) > 0:
        for stock_code in stock_codes:
            outcome = enforce_quota(db, user=current_user, kind=KIND_ANALYSIS)
            if outcome.exceeded:
                # 退还本批次已扣的配额, 保持原子性
                for _i in range(consumed_count):
                    refund_quota(db, user=current_user, kind=KIND_ANALYSIS, on_date=consumed_on_date)
                for credit_outcome in consumed_credit_outcomes:
                    refund_consumed_credits(
                        db,
                        user=current_user,
                        outcome=credit_outcome,
                        related_type="analysis",
                        related_id=stock_code,
                    )
                db.commit()
                return JSONResponse(status_code=402, content=quota_exceeded_payload(outcome))
            credit_outcome = enforce_credits(
                db,
                user=current_user,
                kind=KIND_ANALYSIS,
                related_type="analysis",
                related_id=stock_code,
            )
            if credit_outcome.exceeded:
                # 积分不足：本次分析本次额度 + 本批此前额度全部回滚
                if outcome.consumed:
                    refund_quota(db, user=current_user, kind=KIND_ANALYSIS, on_date=outcome.on_date)
                for _i in range(consumed_count):
                    refund_quota(db, user=current_user, kind=KIND_ANALYSIS, on_date=consumed_on_date)
                for refunded in consumed_credit_outcomes:
                    refund_consumed_credits(
                        db,
                        user=current_user,
                        outcome=refunded,
                        related_type="analysis",
                        related_id=stock_code,
                    )
                db.commit()
                return JSONResponse(status_code=402, content=credit_exceeded_payload(credit_outcome))
            if outcome.consumed:
                consumed_count += 1
                consumed_on_date = consumed_on_date or outcome.on_date
            if credit_outcome.consumed:
                consumed_credit_outcomes.append(credit_outcome)
        if consumed_count > 0:
            db.commit()
        elif consumed_credit_outcomes:
            db.commit()

    # 异步模式：每只股票提交一个独立任务
    try:
        response = _handle_async_analysis_batch(
            stock_codes,
            request,
            user_id=current_user_id,
            refund_analysis_quota=consumed_count > 0,
            quota_refund_date=consumed_on_date,
            refund_analysis_credits=bool(consumed_credit_outcomes),
            analysis_credit_cost=consumed_credit_outcomes[0].cost if consumed_credit_outcomes else 0,
        )
    except Exception:
        # 提交队列前异常时, 退还所有已扣配额
        if consumed_count > 0:
            for _i in range(consumed_count):
                refund_quota(db, user=current_user, kind=KIND_ANALYSIS, on_date=consumed_on_date)
        for credit_outcome in consumed_credit_outcomes:
            refund_consumed_credits(
                db,
                user=current_user,
                outcome=credit_outcome,
                related_type="analysis",
            )
        if consumed_count > 0 or consumed_credit_outcomes:
            db.commit()
        raise

    # 队列内重复任务: 没有真正进入分析, 退还对应配额
    if consumed_count > 0 or consumed_credit_outcomes:
        try:
            payload = json.loads(response.body) if hasattr(response, "body") else None
        except Exception:
            payload = None
        duplicate_count = 0
        if isinstance(payload, dict):
            duplicates = payload.get("duplicates")
            if isinstance(duplicates, list):
                duplicate_count = len(duplicates)
            elif payload.get("error") == "duplicate_task":
                duplicate_count = 1
        for _i in range(min(duplicate_count, consumed_count)):
            refund_quota(db, user=current_user, kind=KIND_ANALYSIS, on_date=consumed_on_date)
        for credit_outcome in consumed_credit_outcomes[:duplicate_count]:
            refund_consumed_credits(
                db,
                user=current_user,
                outcome=credit_outcome,
                related_type="analysis",
            )
        if duplicate_count > 0:
            db.commit()
    return response


def _handle_async_analysis_batch(
    stock_codes: list,
    request: AnalyzeRequest,
    user_id: Optional[int] = None,
    refund_analysis_quota: bool = False,
    quota_refund_date: Optional[date] = None,
    refund_analysis_credits: bool = False,
    analysis_credit_cost: int = 0,
) -> JSONResponse:
    """处理异步分析请求，含批量提交与重复任务识别。

    ``user_id`` 来自 ``current_user.id``。
    """
    task_queue = get_task_queue()

    # 单只股票请求会透传其元数据；批量请求仅保留语义上对整批生效的元数据，
    # 例如导入/图片来源追踪
    is_single = len(stock_codes) == 1
    preserve_batch_metadata = request.selection_source in {"import", "image"}

    stock_name = request.stock_name if is_single else None
    original_query = request.original_query if (is_single or preserve_batch_metadata) else None
    selection_source = request.selection_source if (is_single or preserve_batch_metadata) else None
    notify = getattr(request, "notify", True)
    skills = getattr(request, "skills", None)

    submit_kwargs = dict(
        stock_codes=stock_codes,
        stock_name=stock_name,
        original_query=original_query,
        selection_source=selection_source,
        report_type=request.report_type,
        force_refresh=request.force_refresh,
        notify=notify,
    )
    if user_id is not None:
        submit_kwargs["user_id"] = user_id
    if refund_analysis_quota:
        submit_kwargs["refund_analysis_quota"] = True
        submit_kwargs["quota_refund_date"] = quota_refund_date
    if refund_analysis_credits:
        submit_kwargs["refund_analysis_credits"] = True
        submit_kwargs["analysis_credit_cost"] = int(analysis_credit_cost or 0)
    if skills is not None:
        submit_kwargs["skills"] = skills

    accepted_tasks, duplicate_errors = task_queue.submit_tasks_batch(**submit_kwargs)

    accepted = [
        BatchTaskAcceptedItem(
            task_id=task.task_id,
            stock_code=task.stock_code,
            status="pending",
            message=f"分析任务已加入队列: {task.stock_code}",
        )
        for task in accepted_tasks
    ]
    duplicates = [
        BatchDuplicateTaskItem(
            stock_code=dup.stock_code,
            existing_task_id=dup.existing_task_id,
            message=str(dup),
        )
        for dup in duplicate_errors
    ]

    # 单只股票且全部被拒绝：保持 409 兼容性
    if len(stock_codes) == 1 and duplicates:
        dup = duplicates[0]
        error_response = DuplicateTaskErrorResponse(
            error="duplicate_task",
            message=dup.message,
            stock_code=dup.stock_code,
            existing_task_id=dup.existing_task_id,
        )
        return JSONResponse(
            status_code=409,
            content=error_response.model_dump()
        )

    # 单只股票成功：保持原有响应格式兼容性
    if len(stock_codes) == 1 and accepted:
        task_accepted = TaskAccepted(
            task_id=accepted[0].task_id,
            status="pending",
            message=accepted[0].message,
        )
        return JSONResponse(
            status_code=202,
            content=task_accepted.model_dump()
        )

    # 批量：返回汇总结果
    batch_response = BatchTaskAcceptedResponse(
        accepted=accepted,
        duplicates=duplicates,
        message=f"已提交 {len(accepted)} 个任务，{len(duplicates)} 个重复跳过",
    )
    return JSONResponse(
        status_code=202,
        content=batch_response.model_dump()
    )


def _handle_sync_analysis(
    stock_code: str,
    request: AnalyzeRequest,
    user_id: Optional[int] = None,
) -> AnalysisResultResponse:
    """处理同步分析请求。

    直接执行分析，等待完成后返回结果。
    ``user_id`` 来自 ``current_user.id``。
    """
    import uuid
    from src.services.analysis_service import AnalysisService

    query_id = uuid.uuid4().hex

    try:
        service = AnalysisService()
        result = service.analyze_stock(
            stock_code=stock_code,
            report_type=request.report_type,
            force_refresh=request.force_refresh,
            query_id=query_id,
            send_notification=getattr(request, "notify", True),
            skills=getattr(request, "skills", None),
            user_id=user_id,
        )

        if result is None:
            error_message = service.last_error or f"分析股票 {stock_code} 失败"
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "analysis_failed",
                    "message": error_message,
                }
            )

        # 加载补充信息后再组装结构化报告
        report_data = result.get("report", {})
        context_snapshot, fundamental_snapshot, price_history = _load_sync_fundamental_sources(
            query_id=query_id,
            stock_code=result.get("stock_code", stock_code),
        )
        report = _build_analysis_report(
            report_data,
            query_id,
            stock_code,
            result.get("stock_name"),
            context_snapshot=context_snapshot,
            fallback_fundamental_payload=fundamental_snapshot,
            price_history=price_history,
        )

        return AnalysisResultResponse(
            query_id=query_id,
            stock_code=result.get("stock_code", stock_code),
            stock_name=result.get("stock_name"),
            report=report.model_dump() if report else None,
            created_at=datetime.now().isoformat()
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分析失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"分析过程发生错误: {str(e)}"
            }
        )


# ============================================================
# POST /market-review - 触发大盘复盘
# ============================================================

@router.post(
    "/market-review",
    response_model=MarketReviewAccepted,
    status_code=202,
    responses={
        202: {"description": "大盘复盘任务已接受", "model": MarketReviewAccepted},
        409: {"description": "大盘复盘正在执行", "model": ErrorResponse},
        500: {"description": "提交失败", "model": ErrorResponse},
    },
    summary="触发大盘复盘",
    description="提交一个后台大盘复盘任务，复用 CLI 的大盘复盘链路并保存报告。人工触发不因交易日历跳过，便于 A 股盘后、周末或节假日复盘；接口内部仅提供进程内/单机防重，如多实例（多 Worker/多容器）部署，需结合外部幂等机制避免重复触发。",
)
def trigger_market_review(
    request: Optional[MarketReviewRequest] = Body(None),
    config: Config = Depends(get_config_dep),
    current_user: AppUser = Depends(get_current_user),
) -> MarketReviewAccepted:
    """以非阻塞方式从 Web/API 提交大盘复盘任务。"""
    request = request or MarketReviewRequest()
    current_user_id = _current_user_id_or_none(current_user)

    # 进程内/单机级别的防重锁，避免在同一进程内并发触发多次复盘
    lock_token = _try_acquire_market_review_lock(config)
    if lock_token is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "duplicate_market_review",
                "message": "大盘复盘正在执行中，请稍后再试",
            },
        )

    try:
        task_id = uuid.uuid4().hex
        task = get_task_queue().submit_background_task(
            lambda: _run_market_review_background(
                request.send_notification,
                # 手动入口始终遵循配置的 A 股区域；交易日过滤仅属于自动调度。
                override_region=None,
                lock_token=lock_token,
                config=config,
                query_id=task_id,
            ),
            stock_code="market_review",
            stock_name="大盘复盘",
            message="大盘复盘任务已提交",
            task_id=task_id,
            user_id=current_user_id,
        )
    except Exception:
        # 提交后台任务失败：立即释放锁，避免后续请求持续被拦截
        _release_market_review_lock(lock_token)
        raise

    return MarketReviewAccepted(
        status="accepted",
        message="大盘复盘任务已提交，完成后会保存报告并按配置推送通知",
        send_notification=request.send_notification,
        task_id=task.task_id,
    )


# ============================================================
# GET /tasks - 获取任务列表
# ============================================================

@router.get(
    "/tasks",
    response_model=TaskListResponse,
    responses={
        200: {"description": "任务列表"},
    },
    summary="获取分析任务列表",
    description="获取当前所有分析任务，可按状态筛选"
)
def get_task_list(
    status: Optional[str] = Query(
        None,
        description="筛选状态：pending, processing, completed, failed（支持逗号分隔多个）"
    ),
    limit: int = Query(20, description="返回数量限制", ge=1, le=100),
    current_user: AppUser = Depends(get_current_user),
) -> TaskListResponse:
    """返回当前用户在内存任务队列中的快照。"""
    task_queue = get_task_queue()
    current_user_id = _current_user_id_or_none(current_user)

    # 拉取当前用户可见的全部任务
    all_tasks = task_queue.list_all_tasks(limit=limit, user_id=current_user_id)

    # 按状态过滤，支持逗号分隔的多个状态
    if status:
        status_list = [s.strip().lower() for s in status.split(",")]
        all_tasks = [t for t in all_tasks if t.status.value in status_list]

    # 拉取统计信息，与过滤后的列表并存用于前端展示
    stats = task_queue.get_task_stats(user_id=current_user_id)

    # 转换为对外 schema
    task_infos = [
        TaskInfo(
            task_id=t.task_id,
            stock_code=t.stock_code,
            stock_name=t.stock_name,
            status=t.status.value,
            progress=t.progress,
            message=t.message,
            report_type=t.report_type,
            created_at=t.created_at.isoformat(),
            started_at=t.started_at.isoformat() if t.started_at else None,
            completed_at=t.completed_at.isoformat() if t.completed_at else None,
            error=t.error,
            original_query=t.original_query,
            selection_source=t.selection_source,
            skills=getattr(t, "skills", None),
        )
        for t in all_tasks
    ]

    return TaskListResponse(
        total=stats["total"],
        pending=stats["pending"],
        processing=stats["processing"],
        tasks=task_infos,
    )


# ============================================================
# GET /tasks/stream - SSE 实时推送
# ============================================================

@router.get(
    "/tasks/stream",
    responses={
        200: {"description": "SSE 事件流", "content": {"text/event-stream": {}}},
    },
    summary="任务状态 SSE 流",
    description="通过 Server-Sent Events 实时推送任务状态变化"
)
async def task_stream(current_user: AppUser = Depends(get_current_user)):
    """SSE 任务状态流。

    事件类型：
    - connected: 连接成功
    - task_created: 新任务创建
    - task_started: 任务开始执行
    - task_progress: 任务阶段进度更新
    - task_completed: 任务完成
    - task_failed: 任务失败
    - heartbeat: 心跳（每 30 秒）

    Returns:
        StreamingResponse: SSE 事件流
    """
    current_user_id = _current_user_id_or_none(current_user)

    async def event_generator():
        """为单个 SSE 客户端产出任务生命周期事件与心跳帧。"""
        task_queue = get_task_queue()
        event_queue: asyncio.Queue = asyncio.Queue()

        # 连接建立后立即告知客户端
        yield _format_sse_event("connected", {"message": "Connected to task stream"})

        # 先把当前进行中的任务同步给客户端，避免前端错失启动时的状态
        pending_tasks = task_queue.list_pending_tasks(user_id=current_user_id)
        for task in pending_tasks:
            yield _format_sse_event("task_created", task.to_dict())

        # 订阅任务事件，按用户隔离
        task_queue.subscribe(event_queue, user_id=current_user_id)

        try:
            while True:
                try:
                    # 等待事件，超时后发送心跳保持连接
                    event = await asyncio.wait_for(event_queue.get(), timeout=30)
                    yield _format_sse_event(event["type"], event["data"])
                except asyncio.TimeoutError:
                    # 30s 心跳，防止反代/浏览器超时断开
                    yield _format_sse_event("heartbeat", {
                        "timestamp": datetime.now().isoformat()
                    })
        except asyncio.CancelledError:
            logger.debug("SSE client disconnected, cancelling event generator")
            raise
        finally:
            # 客户端断开时务必退订，避免队列里堆积失效订阅者
            task_queue.unsubscribe(event_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲，保证 SSE 实时推送
        }
    )


def _format_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """构造一帧 Server-Sent Event，data 部分为 UTF-8 JSON。"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ============================================================
# GET /status/{task_id} - 查询单个任务状态
# ============================================================

@router.get(
    "/status/{task_id}",
    response_model=TaskStatus,
    responses={
        200: {"description": "任务状态"},
        404: {"description": "任务不存在", "model": ErrorResponse},
    },
    summary="查询分析任务状态",
    description="根据 task_id 查询单个任务的状态"
)
def get_analysis_status(
    task_id: str,
    current_user: AppUser = Depends(get_current_user),
) -> TaskStatus:
    """优先从内存任务队列查询，再回退到持久化历史记录。"""
    current_user_id = _current_user_id_or_none(current_user)
    # 1. 先从任务队列查询
    task_queue = get_task_queue()
    task = task_queue.get_task(task_id, user_id=current_user_id)

    if task:
        result: Optional[AnalysisResultResponse] = None
        market_review_report = None

        if task.status == TaskStatusEnum.COMPLETED and isinstance(task.result, dict):
            if task.stock_code == "market_review":
                # 大盘复盘任务：直接透传文本报告
                report_text = task.result.get("result")
                if isinstance(report_text, str) and report_text.strip():
                    market_review_report = report_text
            else:
                try:
                    result = AnalysisResultResponse.model_validate(task.result)
                except Exception:
                    # 旧版本/异常 schema：回退为空结果而不是直接报错
                    logger.warning(
                        "解析任务结果失败，回退为空返回: task_id=%s",
                        task.task_id,
                    )

        return TaskStatus(
            task_id=task.task_id,
            status=task.status.value,
            progress=task.progress,
            result=result,
            market_review_report=market_review_report,
            error=task.error,
            stock_name=task.stock_name,
            original_query=task.original_query,
            selection_source=task.selection_source,
            skills=getattr(task, "skills", None),
        )

    # 2. 从数据库查询已完成的记录
    try:
        from src.storage import DatabaseManager
        db = DatabaseManager.get_instance()
        records = db.get_analysis_history(query_id=task_id, limit=1, user_id=current_user_id)

        if records:
            record = records[0]
            raw_result = parse_json_field(record.raw_result)
            if getattr(record, "report_type", None) == "market_review":
                market_review_report = None
                if isinstance(raw_result, dict):
                    report_text = raw_result.get("raw_response") or raw_result.get("market_review_report")
                    if isinstance(report_text, str) and report_text.strip():
                        market_review_report = report_text
                # 兼容旧格式：把正文当作复盘报告兜底
                if not market_review_report and record.news_content:
                    market_review_report = record.news_content

                return TaskStatus(
                    task_id=task_id,
                    status="completed",
                    progress=100,
                    result=None,
                    market_review_report=market_review_report,
                    error=None,
                    stock_name=record.name,
                )

            model_used = normalize_model_used(
                (raw_result or {}).get("model_used") if isinstance(raw_result, dict) else None
            )
            report_language = normalize_report_language(
                (raw_result or {}).get("report_language") if isinstance(raw_result, dict) else None
            )
            stock_name = get_localized_stock_name(record.name, record.code, report_language)

            # 从 context_snapshot 中提取当前价格、涨跌幅与技能标签
            current_price = None
            change_pct = None
            skills = None
            context_snapshot = parse_json_field(getattr(record, 'context_snapshot', None))
            if context_snapshot and isinstance(context_snapshot, dict):
                raw_skills = context_snapshot.get("skills")
                if isinstance(raw_skills, list):
                    skills = [str(skill) for skill in raw_skills]
                enhanced_context = context_snapshot.get('enhanced_context') or {}
                realtime = enhanced_context.get('realtime') or {}
                current_price = realtime.get('price')
                change_pct = realtime.get('change_pct')
                realtime_quote_raw = context_snapshot.get('realtime_quote_raw') or {}
                if current_price is None:
                    current_price = realtime_quote_raw.get('price')
                if change_pct is None:
                    change_pct = realtime_quote_raw.get('change_pct')
                if change_pct is None:
                    # 兼容历史字段名 pct_chg
                    change_pct = realtime_quote_raw.get('pct_chg')

            # 基于 DB 记录构建结构化报告，使已完成任务返回真实数据
            report_dict = AnalysisReport(
                meta=ReportMeta(
                    id=record.id,
                    query_id=task_id,
                    stock_code=record.code,
                    stock_name=stock_name,
                    report_type=getattr(record, 'report_type', None),
                    report_language=report_language,
                    created_at=record.created_at.isoformat() if record.created_at else None,
                    model_used=model_used,
                    current_price=current_price,
                    change_pct=change_pct,
                ),
                summary=ReportSummary(
                    sentiment_score=record.sentiment_score,
                    operation_advice=record.operation_advice,
                    trend_prediction=record.trend_prediction,
                    analysis_summary=record.analysis_summary,
                ),
                strategy=ReportStrategy(
                    ideal_buy=_stringify_report_strategy_value(getattr(record, 'ideal_buy', None)),
                    secondary_buy=_stringify_report_strategy_value(getattr(record, 'secondary_buy', None)),
                    stop_loss=_stringify_report_strategy_value(getattr(record, 'stop_loss', None)),
                    take_profit=_stringify_report_strategy_value(getattr(record, 'take_profit', None)),
                ),
                details=ReportDetails(
                    news_content=getattr(record, "news_content", None),
                    empty_news_disclosure=empty_news_disclosure_from_stored(
                        raw_result, context_snapshot, report_language
                    ),
                    raw_result=raw_result,
                    context_snapshot=context_snapshot,
                    stock_profile=raw_result.get("stock_profile") if isinstance(raw_result, dict) else None,
                    market_structure_context=(
                        extract_market_structure_context(context_snapshot)
                        or (raw_result.get("market_structure_context") if isinstance(raw_result, dict) else None)
                    ),
                ),
            ).model_dump()
            return TaskStatus(
                task_id=task_id,
                status="completed",
                progress=100,
                result=AnalysisResultResponse(
                    query_id=task_id,
                    stock_code=record.code,
                    stock_name=stock_name,
                    report=report_dict,
                    created_at=record.created_at.isoformat() if record.created_at else datetime.now().isoformat()
                ),
                error=None,
                skills=skills,
            )

    except Exception as e:
        logger.error(f"查询任务状态失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"查询任务状态失败: {str(e)}"
            }
        )

    # 3. 队列和数据库都没有该任务，视为不存在或已过期
    raise HTTPException(
        status_code=404,
        detail={
            "error": "not_found",
            "message": f"任务 {task_id} 不存在或已过期"
        }
    )


# ============================================================
# 辅助函数
# ============================================================

def _load_sync_fundamental_sources(
    query_id: str,
    stock_code: str,
) -> tuple[Optional[Any], Optional[Dict[str, Any]], list[Dict[str, Any]]]:
    """为同步分析响应加载可选的上下文、基本面与价格历史信息。

    这些补充信息仅用于丰富同步响应的结构化报告；读取失败时 fail-open，避免分析已经
    成功但附加历史/基本面读取异常导致整个接口失败。
    """
    try:
        from src.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        records = db.get_analysis_history(query_id=query_id, code=stock_code, limit=1)
        context_snapshot = None
        if records:
            context_snapshot = parse_json_field(getattr(records[0], "context_snapshot", None))

        fallback_fundamental = db.get_latest_fundamental_snapshot(
            query_id=query_id,
            code=stock_code,
        )
        price_history = []
        for row in reversed(db.get_latest_data(stock_code, days=60)):
            item = row.to_dict()
            row_date = item.get("date")
            if hasattr(row_date, "isoformat"):
                item["date"] = row_date.isoformat()
            price_history.append(item)

        return context_snapshot, fallback_fundamental, price_history
    except Exception as e:
        # 任何加载失败都吞掉并降级返回空，避免影响主分析结果返回
        logger.debug(
            "load sync fundamental sources failed (fail-open): query_id=%s stock_code=%s err=%s",
            query_id,
            stock_code,
            e,
        )
        return None, None, []


def _stringify_report_strategy_value(value: Any) -> Optional[str]:
    """将策略点位转换为历史 schema 期望的字符串类型。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _build_analysis_report(
        report_data: Dict[str, Any],
        query_id: str,
        stock_code: str,
        stock_name: Optional[str] = None,
        context_snapshot: Optional[Any] = None,
        fallback_fundamental_payload: Optional[Dict[str, Any]] = None,
        price_history: Optional[list[Dict[str, Any]]] = None,
) -> AnalysisReport:
    """组装对外公开的结构化分析报告。

    原始分析结果可能来自同步执行、历史记录或不同版本的报告生成器；这里统一补齐
    meta/summary/strategy/details，并从 context/fallback 中提取财务、分红、板块和
    价格历史字段，保证前端消费的报告结构稳定。
    """
    meta_data = report_data.get("meta", {})
    summary_data = report_data.get("summary", {})
    strategy_data = report_data.get("strategy", {})
    details_data = report_data.get("details", {})
    # 报告语言按 meta -> context_snapshot -> 全局默认的顺序回退
    report_language = normalize_report_language(
        meta_data.get("report_language")
        or (context_snapshot or {}).get("report_language")
        or getattr(Config.get_instance(), "report_language", "zh")
    )
    localized_stock_name = get_localized_stock_name(
        meta_data.get("stock_name", stock_name),
        meta_data.get("stock_code", stock_code),
        report_language,
    )

    meta = ReportMeta(
        query_id=meta_data.get("query_id", query_id),
        stock_code=meta_data.get("stock_code", stock_code),
        stock_name=localized_stock_name,
        report_type=meta_data.get("report_type", "detailed"),
        report_language=report_language,
        created_at=meta_data.get("created_at", datetime.now().isoformat()),
        current_price=meta_data.get("current_price"),
        change_pct=meta_data.get("change_pct"),
        model_used=normalize_model_used(meta_data.get("model_used")),
    )

    summary = ReportSummary(
        analysis_summary=summary_data.get("analysis_summary"),
        operation_advice=summary_data.get("operation_advice"),
        trend_prediction=summary_data.get("trend_prediction"),
        sentiment_score=summary_data.get("sentiment_score"),
        sentiment_label=summary_data.get("sentiment_label")
    )

    strategy = None
    if strategy_data:
        strategy = ReportStrategy(
            ideal_buy=_stringify_report_strategy_value(strategy_data.get("ideal_buy")),
            secondary_buy=_stringify_report_strategy_value(strategy_data.get("secondary_buy")),
            stop_loss=_stringify_report_strategy_value(strategy_data.get("stop_loss")),
            take_profit=_stringify_report_strategy_value(strategy_data.get("take_profit"))
        )

    extracted_fundamental = extract_fundamental_detail_fields(
        context_snapshot=context_snapshot,
        fallback_fundamental_payload=fallback_fundamental_payload,
    )
    extracted_boards = extract_board_detail_fields(
        context_snapshot=context_snapshot,
        fallback_fundamental_payload=fallback_fundamental_payload,
    )
    market_structure_context = (
        extract_market_structure_context(context_snapshot)
        or (details_data.get("market_structure_context") if isinstance(details_data, dict) else None)
    )
    details = None
    has_board_details = bool(extracted_boards.get("belong_boards")) or extracted_boards.get("sector_rankings") is not None
    if (
        details_data
        or any(extracted_fundamental.values())
        or has_board_details
        or market_structure_context is not None
        or context_snapshot is not None
        or price_history
        or details_data.get("stock_profile")
    ):
        details = ReportDetails(
            news_content=details_data.get("news_summary") or details_data.get("news_content"),
            empty_news_disclosure=empty_news_disclosure_from_stored(
                details_data, context_snapshot, report_language
            ),
            raw_result=details_data,
            context_snapshot=context_snapshot,
            financial_report=extracted_fundamental.get("financial_report"),
            dividend_metrics=extracted_fundamental.get("dividend_metrics"),
            stock_profile=details_data.get("stock_profile"),
            belong_boards=extracted_boards.get("belong_boards"),
            sector_rankings=extracted_boards.get("sector_rankings"),
            market_structure_context=market_structure_context,
            price_history=price_history or [],
        )

    return AnalysisReport(
        meta=meta,
        summary=summary,
        strategy=strategy,
        details=details
    )
