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
    StockProfileResponse,
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
from src.services.stock_profile_service import InvalidStockProfileCode, StockProfileService
from src.repositories.stock_index_repo import StockIndexRepository

logger = logging.getLogger(__name__)

router = APIRouter()
_SEARCH_RATE_WINDOW_SEC = 60
_SEARCH_RATE_MAX_REQUESTS = 60
_search_rate_lock = threading.Lock()
# key: 客户端标识（IP），value: (窗口内已请求次数, 窗口起始时间戳)
# 采用滑动窗口而非固定窗口，避免窗口边界处的突发流量穿透
_search_rate_state: dict[str, tuple[int, float]] = {}

# 搜索路由必须在 /{stock_code}/... 动态路由之前定义，否则会被当作股票代码。
ALLOWED_MIME_STR = ", ".join(ALLOWED_MIME)


@router.get("/{stock_code}/profile", response_model=StockProfileResponse, summary="获取轻量股票画像")
def get_stock_profile(
    stock_code: str,
    history_days: int = Query(60, ge=5, le=250),
) -> StockProfileResponse:
    """聚合行情、日线和基本面；各区块独立降级，不触发 LLM 分析计费。"""
    try:
        return StockProfileResponse(**StockProfileService().get_profile(stock_code, history_days=history_days))
    except InvalidStockProfileCode as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_stock_code", "message": str(exc)})
    except Exception:
        logger.exception("股票画像聚合失败: %s", stock_code)
        raise HTTPException(status_code=500, detail={"error": "profile_failed", "message": "股票画像暂时不可用"})


def _check_search_rate_limit(key: str) -> bool:
    """判断指定客户端是否还能再发起一次股票搜索请求。"""
    now = time.time()
    with _search_rate_lock:
        # 顺手清理过期窗口，避免长期运行进程中内存随客户端 IP 无界增长。
        # 清理逻辑放在每次请求时执行，属于"被动 GC"，不额外占用线程。
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
    # 基于客户端 IP 做限流，而非用户身份；
    # 这样即使未登录用户也能被保护，同时防止恶意脚本通过更换账号绕过限制。
    if not _check_search_rate_limit(client_host):
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limited", "message": "股票搜索请求过于频繁，请稍后再试"},
        )
    try:
        return {"items": StockIndexRepository().search(q, limit=limit)}
    except Exception as e:
        # 搜索失败通常是索引文件损坏或磁盘 IO 问题，记 error 并返回模糊消息，
        # 避免把内部异常细节暴露给客户端。
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

    # 兼容形如 "image/jpeg; charset=utf-8" 的 Content-Type，只取媒体类型部分。
    # 某些客户端或代理会自动追加 charset，若不截断会导致 MIME 白名单误判。
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_MIME:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_type",
                "message": f"不支持的类型: {content_type}。允许: {ALLOWED_MIME_STR}",
            },
        )

    # 先读取限定大小，再探测是否还有剩余字节；超过上限就拒绝，避免完整读入大文件。
    # 这是防御性编程：即使客户端绕过前端限制上传超大文件，服务端也能在 OOM 前拦截。
    try:
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
        # 服务层用 ValueError 表达输入不合法或解析失败，映射为 400。
        # 这类异常属于可预期的业务错误，不需要记录 error 级别日志。
        raise HTTPException(status_code=400, detail={"error": "extract_failed", "message": str(e)})
    except Exception as e:
        # 未知异常记录完整堆栈，便于排查 Vision LLM 或依赖服务故障。
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
        # 剪贴板纯文本路径：直接读取 body.text 后交给服务层解析。
        # 不在这里做文本长度限制，因为 parse_import_from_text 内部会校验；
        # 这样可以把业务规则收敛到服务层，endpoint 只负责协议适配。
        try:
            body = await request.json()
        except Exception as e:
            # 捕获所有 JSON 解析异常（包括 malformed JSON、编码错误等），
            # 统一映射为 400，避免内部异常细节泄露给客户端。
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
        # 文件上传路径：先做大小校验，再分块读取防止一次性载入超大文件。
        # 这里使用 "先读后探" 策略：读取 MAX_FILE_BYTES 后若还有剩余字节，
        # 说明文件超过限制，立即拒绝，避免内存被撑爆。
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
    # raw_text=None 表示不暴露内部原始文本，减少信息泄露面。
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
        # 若使用 async def，阻塞调用会卡住事件循环，导致并发性能骤降。
        result = service.get_realtime_quote(stock_code)

        if result is None:
            # 服务层返回 None 而非抛异常，说明外部数据源无此标的或接口超时
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
        # 直接透传 HTTPException，避免被外层 except Exception 捕获后二次包装。
        raise
    except Exception as e:
        # 兜底异常：记录原始异常后转换为通用 500，避免堆栈信息泄露。
        # 生产环境不应把内部异常详情返回给客户端。
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
        # 若使用 async def，阻塞调用会卡住事件循环，导致并发性能骤降。
        result = service.get_history_data(
            stock_code=stock_code,
            period=period,
            days=days
        )

        # 服务层返回 dict 列表，这里收敛为公开 Pydantic 响应模型，
        # 隔离内部数据结构与外部 API 契约，避免前端直接依赖底层字段名。
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
        # 服务层用 ValueError 表达不支持的周期（如 "hourly"），映射为 422 请求参数错误。
        # 不记 error 日志，因为这是客户端输入问题，非服务端故障。
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unsupported_period",
                "message": str(e)
            }
        )
    except HTTPException:
        # 直接透传 HTTPException，避免被外层 except Exception 捕获后二次包装。
        raise
    except Exception as e:
        # 兜底异常：记录原始异常后转换为通用 500，避免堆栈信息泄露。
        # 生产环境不应把内部异常详情返回给客户端。
        logger.error(f"获取历史行情失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"获取历史行情失败: {str(e)}"
            }
        )
