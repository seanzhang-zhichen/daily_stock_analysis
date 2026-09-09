# -*- coding: utf-8 -*-
"""Stock data, import parsing, and image extraction endpoints.

本模块提供公开股票搜索、图片/文本/文件解析、实时行情和历史 K 线查询。上传类接口
在进入服务层前先做 MIME、大小和格式校验；行情接口保持同步函数，让 FastAPI 在线程池
中执行可能阻塞的数据源调用。
"""

import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from api.v1.schemas.stocks import (
    ExtractFromImageResponse,
    ExtractItem,
    KLineData,
    StockHistoryResponse,
    StockQuote,
)
from api.v1.schemas.common import ErrorResponse
from src.services.image_stock_extractor import (
    ALLOWED_MIME,
    MAX_SIZE_BYTES,
    extract_stock_codes_from_image,
)
from src.services.import_parser import (
    MAX_FILE_BYTES,
    parse_import_from_bytes,
    parse_import_from_text,
)
from src.services.stock_service import StockService
from src.repositories.stock_index_repo import StockIndexRepository

logger = logging.getLogger(__name__)

router = APIRouter()
_SEARCH_RATE_WINDOW_SEC = 60
_SEARCH_RATE_MAX_REQUESTS = 60
_search_rate_lock = threading.Lock()
_search_rate_state: dict[str, tuple[int, float]] = {}

# 搜索路由必须在 /{stock_code}/... 动态路由之前定义，否则会被当作股票代码。
ALLOWED_MIME_STR = ", ".join(ALLOWED_MIME)


def _check_search_rate_limit(key: str) -> bool:
    """判断指定客户端是否还能再发起一次股票搜索请求。"""
    now = time.time()
    with _search_rate_lock:
        # 顺手清理过期窗口，避免长期运行进程中内存随客户端 IP 无界增长。
        for state_key, (_, started_at) in list(_search_rate_state.items()):
            if now - started_at > _SEARCH_RATE_WINDOW_SEC:
                _search_rate_state.pop(state_key, None)
        count, started_at = _search_rate_state.get(key, (0, now))
        if now - started_at > _SEARCH_RATE_WINDOW_SEC:
            # 窗口已过期，重置为本次首次请求
            _search_rate_state[key] = (1, now)
            return True
        if count >= _SEARCH_RATE_MAX_REQUESTS:
            return False
        _search_rate_state[key] = (count + 1, started_at)
        return True


@router.get(
    "/search",
    summary="搜索股票索引",
    description="公开股票搜索接口，用于前端 autocomplete。默认限 20 条，最大 50 条。",
)
def search_stock_index(
    request: Request,
    q: str = Query("", min_length=1, max_length=64, description="股票代码、中文名、拼音或别名"),
    limit: int = Query(20, ge=1, le=50, description="返回数量"),
) -> dict:
    """Search the local stock index for autocomplete suggestions.

    在本地股票索引中按代码、中文名、拼音或别名进行模糊匹配，
    用于前端 autocomplete 下拉。
    """
    client_host = request.client.host if request.client else "unknown"
    if not _check_search_rate_limit(client_host):
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limited", "message": "股票搜索请求过于频繁，请稍后再试"},
        )
    try:
        return {"items": StockIndexRepository().search(q, limit=limit)}
    except Exception as e:
        logger.error("股票搜索失败: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "股票搜索失败"},
        )


@router.post(
    "/extract-from-image",
    response_model=ExtractFromImageResponse,
    responses={
        200: {"description": "提取的股票代码"},
        400: {"description": "图片无效", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="从图片提取股票代码",
    description="上传截图/图片，通过 Vision LLM 提取股票代码。支持 JPEG、PNG、WebP、GIF，最大 5MB。",
)
def extract_from_image(
    file: Optional[UploadFile] = File(None, description="图片文件（表单字段名 file）"),
    include_raw: bool = Query(False, description="是否在结果中包含原始 LLM 响应"),
) -> ExtractFromImageResponse:
    """通过 Vision 流水线从上传图片中提取候选股票代码。"""
    if not file or not file.filename:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "message": "未提供文件，请使用表单字段 file 上传图片"},
        )

    # 兼容形如 "image/jpeg; charset=utf-8" 的 Content-Type，只取媒体类型部分
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_MIME:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_type",
                "message": f"不支持的类型: {content_type}。允许: {ALLOWED_MIME_STR}",
            },
        )

    try:
        # 先读取限定大小，再探测是否还有剩余字节；超过上限就拒绝，避免完整读入大文件。
        data = file.file.read(MAX_SIZE_BYTES)
        if file.file.read(1):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "file_too_large",
                    "message": f"图片超过 {MAX_SIZE_BYTES // (1024 * 1024)}MB 限制",
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"读取上传文件失败: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": "read_failed", "message": "读取上传文件失败"},
        )

    try:
        items, raw_text = extract_stock_codes_from_image(data, content_type)
        extract_items = [
            ExtractItem(code=code, name=name, confidence=conf) for code, name, conf in items
        ]
        return ExtractFromImageResponse(
            items=extract_items,
            raw_text=raw_text if include_raw else None,
        )
    except ValueError as e:
        # 服务层用 ValueError 表达输入不合法或解析失败，映射为 400
        raise HTTPException(status_code=400, detail={"error": "extract_failed", "message": str(e)})
    except Exception as e:
        logger.error(f"图片提取失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "图片提取失败"},
        )


@router.post(
    "/parse-import",
    response_model=ExtractFromImageResponse,
    responses={
        200: {"description": "解析结果"},
        400: {"description": "未提供数据或解析失败", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="解析 CSV/Excel/剪贴板",
    description="上传 CSV/Excel 文件或粘贴文本，自动解析股票代码。文件上限 2MB，文本上限 100KB。",
)
async def parse_import(request: Request) -> ExtractFromImageResponse:
    """从上传文件或粘贴文本中解析候选股票代码。

    支持 ``multipart/form-data`` 的 ``file`` 字段，以及
    ``application/json`` 的 ``{"text": "..."}``。两类输入分别走服务层解析，
    endpoint 只负责读取、大小限制和错误转换。
    """
    content_type = (request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        # 剪贴板纯文本路径：直接读取 body.text 后交给服务层解析
        try:
            body = await request.json()
        except Exception as e:
            logger.warning("[parse_import] JSON parse failed: %s", e)
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_json", "message": f"JSON 解析失败: {e}"},
            )
        text = body.get("text") if isinstance(body, dict) else None
        if not text or not isinstance(text, str):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "message": "未提供 text，请使用 {\"text\": \"...\"}"},
            )
        try:
            items = parse_import_from_text(text)
        except ValueError as e:
            text_bytes = len(text.encode("utf-8"))
            logger.warning(
                "[parse_import] parse_import_from_text failed: text_bytes=%d, error=%s",
                text_bytes,
                e,
            )
            raise HTTPException(status_code=400, detail={"error": "parse_failed", "message": str(e)})
    elif "multipart" in content_type:
        # 文件上传路径：先做大小校验，再分块读取防止一次性载入超大文件
        form = await request.form()
        file = form.get("file")
        if not file or not hasattr(file, "read"):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "message": "未提供文件，请使用表单字段 file"},
            )
        file_size = getattr(file, "size", None)
        if isinstance(file_size, int) and file_size > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "file_too_large",
                    "message": f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)}MB 限制",
                },
            )
        try:
            data = file.file.read(MAX_FILE_BYTES)
            if file.file.read(1):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "file_too_large",
                        "message": f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)}MB 限制",
                    },
                )
        except HTTPException:
            raise
        except Exception as e:
            filename = getattr(file, "filename", None) or ""
            size = getattr(file, "size", None)
            logger.warning(
                "[parse_import] file read failed: filename=%r, size=%s, error=%s",
                filename,
                size,
                e,
            )
            raise HTTPException(
                status_code=400,
                detail={"error": "read_failed", "message": "读取文件失败"},
            )
        filename = getattr(file, "filename", None) or ""
        try:
            items = parse_import_from_bytes(data, filename=filename)
        except ValueError as e:
            ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            logger.warning(
                "[parse_import] parse_import_from_bytes failed: filename=%r, ext=%r, bytes=%d, error=%s",
                filename,
                ext,
                len(data),
                e,
            )
            raise HTTPException(status_code=400, detail={"error": "parse_failed", "message": str(e)})
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "bad_request",
                "message": "请使用 multipart/form-data 上传文件，或 application/json 提交 {\"text\": \"...\"}",
            },
        )

    extract_items = [
        ExtractItem(code=code, name=name, confidence=conf)
        for code, name, conf in items
    ]
    return ExtractFromImageResponse(items=extract_items, raw_text=None)


@router.get(
    "/{stock_code}/quote",
    response_model=StockQuote,
    responses={
        200: {"description": "行情数据"},
        404: {"description": "股票不存在", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="获取股票实时行情",
    description="获取指定股票的最新行情数据"
)
def get_stock_quote(stock_code: str) -> StockQuote:
    """返回指定股票代码的最新标准化实时行情。"""
    try:
        service = StockService()

        # 同步数据源可能阻塞网络/IO；使用 def 让 FastAPI 在线程池中执行。
        result = service.get_realtime_quote(stock_code)

        if result is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "not_found",
                    "message": f"未找到股票 {stock_code} 的行情数据"
                }
            )

        return StockQuote(
            stock_code=result.get("stock_code", stock_code),
            stock_name=result.get("stock_name"),
            current_price=result.get("current_price", 0.0),
            change=result.get("change"),
            change_percent=result.get("change_percent"),
            open=result.get("open"),
            high=result.get("high"),
            low=result.get("low"),
            prev_close=result.get("prev_close"),
            volume=result.get("volume"),
            amount=result.get("amount"),
            update_time=result.get("update_time")
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取实时行情失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"获取实时行情失败: {str(e)}"
            }
        )


@router.get(
    "/{stock_code}/history",
    response_model=StockHistoryResponse,
    responses={
        200: {"description": "历史行情数据"},
        422: {"description": "不支持的周期参数", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="获取股票历史行情",
    description="获取指定股票的历史 K 线数据"
)
def get_stock_history(
    stock_code: str,
    period: str = Query("daily", description="K 线周期", pattern="^(daily|weekly|monthly)$"),
    days: int = Query(30, ge=1, le=365, description="获取天数")
) -> StockHistoryResponse:
    """返回指定股票代码与周期的历史 K 线数据。"""
    try:
        service = StockService()

        # 同步数据源可能阻塞网络/IO；使用 def 让 FastAPI 在线程池中执行。
        result = service.get_history_data(
            stock_code=stock_code,
            period=period,
            days=days
        )

        # 服务层返回 dict 列表，这里收敛为公开 Pydantic 响应模型。
        data = [
            KLineData(
                date=item.get("date"),
                open=item.get("open"),
                high=item.get("high"),
                low=item.get("low"),
                close=item.get("close"),
                volume=item.get("volume"),
                amount=item.get("amount"),
                change_percent=item.get("change_percent")
            )
            for item in result.get("data", [])
        ]

        return StockHistoryResponse(
            stock_code=stock_code,
            stock_name=result.get("stock_name"),
            period=period,
            data=data
        )

    except ValueError as e:
        # 服务层用 ValueError 表达不支持的周期，映射为请求参数错误。
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unsupported_period",
                "message": str(e)
            }
        )
    except Exception as e:
        logger.error(f"获取历史行情失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"获取历史行情失败: {str(e)}"
            }
        )
