# -*- coding: utf-8 -*-
"""History and persisted report endpoints.

历史记录接口按当前登录用户隔离数据，支持列表摘要、批量删除、结构化报告详情、
关联新闻和 Markdown 报告输出。详情接口需要兼容旧历史数据，因此会在 endpoint
层补齐语言、本地化展示、实时价格兜底和结构化财务/板块字段。
"""

import logging
from typing import Any, Mapping, Optional

from fastapi import APIRouter, HTTPException, Query, Depends, Body
from fastapi.responses import HTMLResponse, Response

from api.deps import get_current_user, get_database_manager
from api.v1.schemas.history import (
    HistoryListResponse,
    HistoryItem,
    DeleteHistoryRequest,
    DeleteHistoryResponse,
    NewsIntelItem,
    NewsIntelResponse,
    AnalysisReport,
    ReportMeta,
    ReportSummary,
    ReportStrategy,
    ReportDetails,
    MarkdownReportResponse,
    RunDiagnosticSummaryResponse,
    HistoryTrendPoint,
    HistoryTrendResponse,
)
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.run_flow import RunFlowSnapshot
from src.storage import AppUser, DatabaseManager
from src.report_language import (
    get_sentiment_label,
    get_localized_stock_name,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.services.history_service import HistoryService, MarkdownReportGenerationError
from src.services.empty_news import empty_news_disclosure_from_stored
from src.config import get_config
from src.md2img import markdown_to_image
from src.share_image import build_share_image_html, share_image_branding_from_config
from src.utils.data_processing import (
    normalize_model_used,
    extract_fundamental_detail_fields,
    extract_board_detail_fields,
    extract_market_structure_context,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _current_user_id_or_none(current_user: AppUser) -> Optional[int]:
    """返回当前用户的数值 id，便于在测试等无 id 场景下保持服务层容错。"""
    return getattr(current_user, "id", None)


@router.get("/by-code/{stock_code}/trend", response_model=HistoryTrendResponse)
def get_history_trend_by_code(
    stock_code: str,
    limit: int = Query(100, ge=1, le=100),
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> HistoryTrendResponse:
    """返回当前用户在指定股票上的时序 A 股分析结论。"""
    code = str(stock_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail={"error": "invalid_request", "message": "stock_code 不能为空"})
    items = HistoryService(db_manager).get_history_trend_by_code(
        code,
        user_id=_current_user_id_or_none(current_user),
        limit=limit,
    )
    return HistoryTrendResponse(
        stock_code=code,
        stock_name=items[-1].get("stock_name") if items else None,
        items=[HistoryTrendPoint(**{key: value for key, value in item.items() if key != "stock_name"}) for item in items],
    )


@router.delete("/by-code/{stock_code}", response_model=DeleteHistoryResponse)
def delete_history_by_code(
    stock_code: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> DeleteHistoryResponse:
    """删除当前用户范围内某只股票的全部历史分析记录。"""
    code = str(stock_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail={"error": "invalid_request", "message": "stock_code 不能为空"})
    deleted = HistoryService(db_manager).delete_history_by_code(
        code,
        user_id=_current_user_id_or_none(current_user),
    )
    return DeleteHistoryResponse(deleted=deleted)


@router.get("/{record_id}/diagnostics", response_model=RunDiagnosticSummaryResponse)
def get_history_diagnostics(
    record_id: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> RunDiagnosticSummaryResponse:
    """返回当前用户拥有的 A 股报告的脱敏诊断摘要。"""
    summary = HistoryService(db_manager).resolve_and_get_diagnostics(
        record_id,
        user_id=_current_user_id_or_none(current_user),
    )
    if summary is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Analysis report not found"})
    return RunDiagnosticSummaryResponse.model_validate(summary)


@router.get("/{record_id}/flow", response_model=RunFlowSnapshot)
def get_history_run_flow(
    record_id: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> RunFlowSnapshot:
    """返回当前用户拥有的 A 股报告的脱敏运行流（节点、边、事件）。"""
    snapshot = HistoryService(db_manager).resolve_and_get_run_flow(
        record_id,
        user_id=_current_user_id_or_none(current_user),
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Analysis report not found"})
    return RunFlowSnapshot.model_validate(snapshot)


def _history_share_image_payload(result: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    """选择用于填充分享图海报的持久化结构化载荷。"""

    if result.get("report_type") == "market_review":
        # 盘后点评类报告：优先使用上下文快照内的市场点评专用载荷
        context_snapshot = result.get("context_snapshot")
        if isinstance(context_snapshot, Mapping):
            market_payload = context_snapshot.get("market_review_payload")
            if isinstance(market_payload, Mapping):
                return market_payload

    # 默认回退到通用的原始结果字典
    raw_result = result.get("raw_result")
    return raw_result if isinstance(raw_result, Mapping) else None


def _history_share_image_input(
    record_id: str,
    db_manager: DatabaseManager,
    user_id: Optional[int],
) -> tuple[Mapping[str, Any], str]:
    """为 PNG 与桌面端 HTML 渲染器加载同一份用户拥有的报告。"""

    service = HistoryService(db_manager)
    result = service.resolve_and_get_detail(record_id, user_id=user_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"未找到 id/query_id={record_id} 的分析记录",
            },
        )

    try:
        markdown_content = service.get_markdown_report(record_id, user_id=user_id)
    except MarkdownReportGenerationError as exc:
        logger.error("Share image report generation failed for %s: %s", record_id, exc.message)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "generation_failed",
                "message": f"生成分享图片所需报告失败: {exc.message}",
            },
        ) from exc

    if not markdown_content:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"未找到 id/query_id={record_id} 的报告内容",
            },
        )
    return result, markdown_content


@router.get(
    "",
    response_model=HistoryListResponse,
    responses={
        200: {"description": "历史记录列表"},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="获取历史分析列表",
    description="分页获取历史分析记录摘要，支持按股票代码和日期范围筛选"
)
def get_history_list(
    stock_code: Optional[str] = Query(None, description="股票代码筛选"),
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    page: int = Query(1, ge=1, description="页码（从 1 开始）"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> HistoryListResponse:
    """返回当前登录用户的历史分析摘要，支持分页与股票代码/日期范围筛选。"""
    try:
        service = HistoryService(db_manager)

        # HistoryService 内部执行同步 DB/文件 IO；endpoint 用 def 让 FastAPI 在线程池中调度。
        result = service.get_history_list(
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            page=page,
            limit=limit,
            user_id=_current_user_id_or_none(current_user),
        )

        # 服务层返回 dict，endpoint 收敛为公开 schema，避免存储字段直接泄漏给前端。
        items = [
            HistoryItem(
                id=item.get("id"),
                query_id=item.get("query_id", ""),
                stock_code=item.get("stock_code", ""),
                stock_name=item.get("stock_name"),
                report_type=item.get("report_type"),
                sentiment_score=item.get("sentiment_score"),
                operation_advice=item.get("operation_advice"),
                created_at=item.get("created_at")
            )
            for item in result.get("items", [])
        ]

        return HistoryListResponse(
            total=result.get("total", 0),
            page=page,
            limit=limit,
            items=items
        )

    except Exception as e:
        logger.error(f"查询历史列表失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"查询历史列表失败: {str(e)}"
            }
        )


@router.delete(
    "",
    response_model=DeleteHistoryResponse,
    responses={
        200: {"description": "删除成功"},
        400: {"description": "请求参数错误", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="删除历史分析记录",
    description="按历史记录主键 ID 批量删除分析历史"
)
def delete_history_records(
    request: DeleteHistoryRequest = Body(...),
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> DeleteHistoryResponse:
    """在请求 id 去重后删除所选历史记录。"""
    # 用 set 去重 + sorted 保证删除顺序稳定，便于日志与审计对齐
    record_ids = sorted({record_id for record_id in request.record_ids if record_id is not None})
    if not record_ids:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "message": "record_ids 不能为空"
            }
        )

    try:
        service = HistoryService(db_manager)
        deleted = service.delete_history_records(
            record_ids,
            user_id=_current_user_id_or_none(current_user),
        )
        return DeleteHistoryResponse(deleted=deleted)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除历史记录失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"删除历史记录失败: {str(e)}"
            }
        )


@router.get(
    "/{record_id}",
    response_model=AnalysisReport,
    responses={
        200: {"description": "报告详情"},
        404: {"description": "报告不存在", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="获取历史报告详情",
    description="根据分析历史记录 ID 或 query_id 获取完整的历史分析报告"
)
def get_history_detail(
    record_id: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> AnalysisReport:
    """根据数字 id 或历史 query_id 返回一条结构化报告。"""
    try:
        service = HistoryService(db_manager)

        # 先按整数 id 查找，未命中再用 query_id 字符串兜底，兼容老链接
        result = service.resolve_and_get_detail(
            record_id,
            user_id=_current_user_id_or_none(current_user),
        )

        if result is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "not_found",
                    "message": f"未找到 id/query_id={record_id} 的分析记录"
                }
            )

        # 从 context_snapshot 中提取价格信息
        # 注意：使用 `is None` 而非 `or`，避免把 0.0（平盘）误判为缺失值；
        # 同时不混用 `change_60d`（60 日累计涨跌幅）作为日内 change_pct 的兜底。
        current_price = None
        change_pct = None
        context_snapshot = result.get("context_snapshot")
        if context_snapshot and isinstance(context_snapshot, dict):
            # 优先从 enhanced_context.realtime 获取
            enhanced_context = context_snapshot.get("enhanced_context") or {}
            realtime = enhanced_context.get("realtime") or {}
            current_price = realtime.get("price")
            change_pct = realtime.get("change_pct")

            # 缺失时再从 realtime_quote_raw 兜底
            realtime_quote_raw = context_snapshot.get("realtime_quote_raw")
            if not isinstance(realtime_quote_raw, dict):
                realtime_quote_raw = {}
            if current_price is None:
                current_price = realtime_quote_raw.get("price")
            if change_pct is None:
                change_pct = realtime_quote_raw.get("change_pct")
            if change_pct is None:
                # 旧字段名 pct_chg 也作为最后兜底
                change_pct = realtime_quote_raw.get("pct_chg")

        raw_result = result.get("raw_result")
        if not isinstance(raw_result, dict):
            raw_result = {}
        # 报告语言按 row -> raw_result -> context_snapshot 的顺序回退
        report_language = normalize_report_language(
            result.get("report_language")
            or raw_result.get("report_language")
            or (
                context_snapshot.get("report_language")
                if isinstance(context_snapshot, dict)
                else None
            )
        )
        # 按报告语言本地化股票名，未匹配则保持原始名称
        stock_name = get_localized_stock_name(
            result.get("stock_name"),
            result.get("stock_code", ""),
            report_language,
        )

        # 构建结构化响应模型，同时按报告语言本地化名称、建议和趋势文案。
        meta = ReportMeta(
            id=result.get("id"),
            query_id=result.get("query_id", ""),
            stock_code=result.get("stock_code", ""),
            stock_name=stock_name,
            report_type=result.get("report_type"),
            report_language=report_language,
            created_at=result.get("created_at"),
            current_price=current_price,
            change_pct=change_pct,
            model_used=normalize_model_used(result.get("model_used"))
        )

        summary = ReportSummary(
            analysis_summary=result.get("analysis_summary"),
            operation_advice=localize_operation_advice(
                result.get("operation_advice"),
                report_language,
            ),
            trend_prediction=localize_trend_prediction(
                result.get("trend_prediction"),
                report_language,
            ),
            sentiment_score=result.get("sentiment_score"),
            sentiment_label=(
                get_sentiment_label(result.get("sentiment_score"), report_language)
                if result.get("sentiment_score") is not None
                else result.get("sentiment_label")
            )
        )

        strategy = ReportStrategy(
            ideal_buy=result.get("ideal_buy"),
            secondary_buy=result.get("secondary_buy"),
            stop_loss=result.get("stop_loss"),
            take_profit=result.get("take_profit")
        )

        # 从数据库和 context_snapshot 中拉取结构化基本面 / 板块字段
        fallback_fundamental = db_manager.get_latest_fundamental_snapshot(
            query_id=result.get("query_id", ""),
            code=result.get("stock_code", ""),
        )
        extracted_fundamental = extract_fundamental_detail_fields(
            context_snapshot=result.get("context_snapshot"),
            fallback_fundamental_payload=fallback_fundamental,
        )
        extracted_boards = extract_board_detail_fields(
            context_snapshot=result.get("context_snapshot"),
            fallback_fundamental_payload=fallback_fundamental,
        )
        market_structure_context = (
            extract_market_structure_context(result.get("context_snapshot"))
            or (raw_result.get("market_structure_context") if isinstance(raw_result, dict) else None)
        )

        details = ReportDetails(
            news_content=result.get("news_content"),
            empty_news_disclosure=empty_news_disclosure_from_stored(
                raw_result, result.get("context_snapshot"), report_language
            ),
            raw_result=result.get("raw_result"),
            context_snapshot=result.get("context_snapshot"),
            financial_report=extracted_fundamental.get("financial_report"),
            dividend_metrics=extracted_fundamental.get("dividend_metrics"),
            stock_profile=raw_result.get("stock_profile") if isinstance(raw_result, dict) else None,
            belong_boards=extracted_boards.get("belong_boards"),
            sector_rankings=extracted_boards.get("sector_rankings"),
            market_structure_context=market_structure_context,
            price_history=result.get("price_history") or [],
        )

        return AnalysisReport(
            meta=meta,
            summary=summary,
            strategy=strategy,
            details=details
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询历史详情失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"查询历史详情失败: {str(e)}"
            }
        )


@router.get(
    "/{record_id}/news",
    response_model=NewsIntelResponse,
    responses={
        200: {"description": "新闻情报列表"},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="获取历史报告关联新闻",
    description="根据分析历史记录 ID 获取关联的新闻情报列表（为空也返回 200）"
)
def get_history_news(
    record_id: str,
    limit: int = Query(20, ge=1, le=100, description="返回数量限制"),
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> NewsIntelResponse:
    """返回某条历史记录（按 id 或 query_id）所关联的新闻情报。"""
    try:
        service = HistoryService(db_manager)
        items = service.resolve_and_get_news(
            record_id=record_id,
            limit=limit,
            user_id=_current_user_id_or_none(current_user),
        )

        response_items = [
            NewsIntelItem(
                title=item.get("title", ""),
                snippet=item.get("snippet"),
                url=item.get("url", "")
            )
            for item in items
        ]

        return NewsIntelResponse(
            total=len(response_items),
            items=response_items
        )

    except Exception as e:
        logger.error(f"查询新闻情报失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"查询新闻情报失败: {str(e)}"
            }
        )


@router.get(
    "/{record_id}/markdown",
    response_model=MarkdownReportResponse,
    responses={
        200: {"description": "Markdown 格式报告"},
        404: {"description": "报告不存在", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="获取历史报告 Markdown 格式",
    description="根据分析历史记录 ID 获取 Markdown 格式的完整分析报告"
)
def get_history_markdown(
    record_id: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> MarkdownReportResponse:
    """为某条历史记录生成通知风格的 Markdown 报告。"""
    service = HistoryService(db_manager)

    try:
        markdown_content = service.get_markdown_report(
            record_id,
            user_id=_current_user_id_or_none(current_user),
        )
    except MarkdownReportGenerationError as e:
        logger.error(f"Markdown report generation failed for {record_id}: {e.message}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "generation_failed",
                "message": f"生成 Markdown 报告失败: {e.message}"
            }
        )
    except Exception as e:
        logger.error(f"获取 Markdown 报告失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"获取 Markdown 报告失败: {str(e)}"
            }
        )

    if markdown_content is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"未找到 id/query_id={record_id} 的分析记录"
            }
        )

    return MarkdownReportResponse(content=markdown_content)


@router.get(
    "/{record_id}/share-image-html",
    response_class=HTMLResponse,
    responses={
        200: {"description": "供 Web 或桌面端 Chromium 渲染的分享图 HTML"},
        404: {"description": "报告不存在", "model": ErrorResponse},
        413: {"description": "报告内容超过分享图长度上限", "model": ErrorResponse},
        500: {"description": "报告生成失败", "model": ErrorResponse},
    },
    summary="获取历史报告分享图 HTML",
    description="根据当前用户的历史报告与结构化数据生成供浏览器截图的确定性 HTML",
)
def get_history_share_image_html(
    record_id: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> HTMLResponse:
    """为浏览器渲染器构造需要鉴权的 HTML 海报页。"""

    result, markdown_content = _history_share_image_input(
        record_id,
        db_manager,
        _current_user_id_or_none(current_user),
    )
    config = get_config()
    max_chars = getattr(config, "markdown_to_image_max_chars", 15000)
    if len(markdown_content) > max_chars:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "share_image_too_large",
                "message": f"报告内容超过分享图片上限 {max_chars} 字符",
            },
        )

    try:
        html = build_share_image_html(
            markdown_content,
            structured_payload=_history_share_image_payload(result),
            branding=share_image_branding_from_config(config),
        )
    except Exception as exc:
        logger.error("Share image HTML generation failed for %s: %s", record_id, exc)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "generation_failed",
                "message": "生成分享图片内容失败",
            },
        ) from exc

    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/{record_id}/share-image",
    response_class=Response,
    responses={
        200: {"description": "PNG 分享图片", "content": {"image/png": {}}},
        404: {"description": "报告不存在", "model": ErrorResponse},
        500: {"description": "报告生成失败", "model": ErrorResponse},
        503: {"description": "图片渲染器不可用", "model": ErrorResponse},
    },
    summary="生成历史报告分享图片",
    description="根据当前用户的历史报告 Markdown 与结构化数据生成 PNG 分享图片",
)
def get_history_share_image(
    record_id: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
    current_user: AppUser = Depends(get_current_user),
) -> Response:
    """将当前用户拥有的历史报告渲染成可下载的 PNG 分享图。"""

    result, markdown_content = _history_share_image_input(
        record_id,
        db_manager,
        _current_user_id_or_none(current_user),
    )
    config = get_config()
    image_bytes = markdown_to_image(
        markdown_content,
        max_chars=getattr(config, "markdown_to_image_max_chars", 15000),
        structured_payload=_history_share_image_payload(result),
    )
    if image_bytes is None:
        engine = getattr(config, "md2img_engine", "wkhtmltoimage")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "share_image_unavailable",
                "message": f"分享图片生成失败，请检查 {engine} 转图工具是否已安装并可用",
            },
        )

    filename = f"alphalens-report-{result.get('id') or record_id}.png"
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
