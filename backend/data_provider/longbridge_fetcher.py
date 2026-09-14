# -*- coding: utf-8 -*-
"""
===================================
LongbridgeFetcher - 长桥兜底数据源 (Priority 5)
===================================

数据来源：长桥 OpenAPI (https://open.longbridge.com)
特点：覆盖美股 + 港股，可计算量比/换手率/PE 等 yfinance 缺失字段
定位：美股/港股最后兜底数据源

关键策略：
1. 组合 quote + static_info 接口计算 turnover_rate / pe_ratio / total_mv
2. 通过 history_candlesticks 计算 volume_ratio（近5日均量比）
3. 懒加载 QuoteContext，首次调用时才建立连接
4. static_info 进程内短缓存，减少重复请求（默认 24h，可调；见 LONGBRIDGE_STATIC_INFO_TTL_SECONDS）

凭证：`LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN`。
可选：`LONGBRIDGE_STATIC_INFO_TTL_SECONDS`；SDK `language` 取自 `REPORT_LANGUAGE`，`log_path` 为 `{LOG_DIR}/longbridge_sdk.log`；
`LONGBRIDGE_HTTP_URL` / `LONGBRIDGE_QUOTE_WS_URL` / `LONGBRIDGE_TRADE_WS_URL` / `LONGBRIDGE_REGION` （见官方文档默认值）。
"""

import logging
import os
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

import pandas as pd

from .base import BaseFetcher, STANDARD_COLUMNS
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource, safe_float
from .us_index_mapping import is_us_stock_code, is_us_index_code

# 模块级日志记录器，用于输出本模块的诊断信息
logger = logging.getLogger(__name__)

# static_info 缓存默认 TTL：24 小时（86400 秒）
_DEFAULT_STATIC_INFO_TTL = 86400  # 24h
# 连接异常后默认冷却时间：15 秒，防止因频繁重连导致服务抖动
_DEFAULT_CONNECTION_COOLDOWN_SECONDS = 15


def _static_info_ttl_seconds() -> int:
    """
    获取 static_info 缓存的 TTL（秒数）。

    读取环境变量 LONGBRIDGE_STATIC_INFO_TTL_SECONDS，若未设置或格式非法则返回默认值。
    若返回 0，表示禁用缓存，每次请求都会重新拉取 static_info。

    Returns:
        int: 缓存有效期（秒），默认 86400 秒。
    """
    raw = os.getenv("LONGBRIDGE_STATIC_INFO_TTL_SECONDS", "").strip()
    if raw == "":
        return _DEFAULT_STATIC_INFO_TTL
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_STATIC_INFO_TTL


def _connection_cooldown_seconds() -> int:
    """
    获取连接异常后的冷却时间（秒数）。

    读取环境变量 LONGBRIDGE_CONNECTION_COOLDOWN_SECONDS，若未设置或格式非法则返回默认值。
    在冷却期内，系统会抑制重连尝试，避免频繁重连造成抖动。

    Returns:
        int: 冷却时间（秒），默认 15 秒。
    """
    raw = os.getenv("LONGBRIDGE_CONNECTION_COOLDOWN_SECONDS", "").strip()
    if raw == "":
        return _DEFAULT_CONNECTION_COOLDOWN_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_CONNECTION_COOLDOWN_SECONDS


# 区域到 URL 的映射表：根据 LONGBRIDGE_REGION 环境变量自动选择接入点。
# "cn" 对应中国大陆节点，"hk" 对应香港/国际节点。
_REGION_URL_MAP: Dict[str, Dict[str, str]] = {
    "cn": {
        "http_url": "https://openapi.longbridge.cn",
        "quote_ws_url": "wss://openapi-quote.longbridge.cn/v2",
        "trade_ws_url": "wss://openapi-trade.longbridge.cn/v2",
    },
    "hk": {
        "http_url": "https://openapi.longbridge.com",
        "quote_ws_url": "wss://openapi-quote.longbridge.com/v2",
        "trade_ws_url": "wss://openapi-trade.longbridge.com/v2",
    },
}


def _sanitize_longbridge_env() -> None:
    """
    清理空的 LONGBRIDGE_*_URL 环境变量。

    GitHub Actions 通过 ``LONGBRIDGE_HTTP_URL: ${{ vars.X || secrets.X }}`` 注入，
    当变量与密钥都未配置时会解析为空字符串 ``""``。Rust SDK 的 ``Config.from_apikey()``
    会自动读取这些环境变量，而空字符串与「未设置」并不等价——它会让 SDK 使用空白 URL，
    导致 WebSocket 握手失败，并在毫秒级报出 "context dropped" / "Client is closed"。

    同时把 ``LONGBRIDGE_REGION`` 同步到 ``LONGPORT_REGION``，因为 Rust SDK 内部的
    ``is_cn()`` 在判断默认接入点时只检查 ``LONGPORT_REGION``（而非 ``LONGBRIDGE_REGION``）。
    """
    for key in (
        "LONGBRIDGE_HTTP_URL",
        "LONGBRIDGE_QUOTE_WS_URL",
        "LONGBRIDGE_TRADE_WS_URL",
        "LONGBRIDGE_ENABLE_OVERNIGHT",
        "LONGBRIDGE_PUSH_CANDLESTICK_MODE",
        "LONGBRIDGE_PRINT_QUOTE_PACKAGES",
        "LONGBRIDGE_REGION",
        "LONGBRIDGE_STATIC_INFO_TTL_SECONDS",
        "LONGBRIDGE_LOG_PATH",
    ):
        val = os.environ.get(key)
        # 若环境变量存在但值为空字符串，则删除该变量，避免 SDK 误用空值
        if val is not None and val.strip() == "":
            del os.environ[key]
            logger.debug("[Longbridge] 删除空环境变量 %s", key)

    # 应用默认安静输出（false），与 README / docs/full-guide / .env.example 保持一致；
    # SDK 单独运行时可能默认为详细输出。
    if "LONGBRIDGE_PRINT_QUOTE_PACKAGES" not in os.environ:
        os.environ["LONGBRIDGE_PRINT_QUOTE_PACKAGES"] = "false"

    # 设置 SDK 日志路径：优先使用 LOG_DIR 环境变量，若未设置则默认 ./logs
    if not os.environ.get("LONGBRIDGE_LOG_PATH"):
        try:
            log_dir = (os.getenv("LOG_DIR") or "./logs").strip() or "./logs"
            p = Path(log_dir).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            os.environ["LONGBRIDGE_LOG_PATH"] = str(p / "longbridge_sdk.log")
            logger.debug("[Longbridge] 设置 LONGBRIDGE_LOG_PATH=%s",
                         os.environ["LONGBRIDGE_LOG_PATH"])
        except Exception:
            pass

    # 同步 LONGBRIDGE_REGION 到 LONGPORT_REGION，确保 SDK 内部区域判断正确
    region = (os.getenv("LONGBRIDGE_REGION") or "").strip().lower()
    if region:
        if not os.environ.get("LONGPORT_REGION"):
            os.environ["LONGPORT_REGION"] = region
            logger.debug("[Longbridge] 同步 LONGPORT_REGION=%s", region)

        # 根据区域自动填充默认的 HTTP 和 WebSocket URL
        urls = _REGION_URL_MAP.get(region, {})
        for env_name, default_url in (
            ("LONGBRIDGE_HTTP_URL", urls.get("http_url")),
            ("LONGBRIDGE_QUOTE_WS_URL", urls.get("quote_ws_url")),
            ("LONGBRIDGE_TRADE_WS_URL", urls.get("trade_ws_url")),
        ):
            if default_url and not os.environ.get(env_name):
                os.environ[env_name] = default_url
                logger.debug("[Longbridge] 根据 REGION=%s 设置 %s=%s",
                             region, env_name, default_url)


def _longbridge_config_kwargs() -> Dict[str, Any]:
    """
    为 ``Config.from_apikey``（长桥 OpenAPI SDK）构造可选参数。

    通过反射检查 Config.from_apikey 的签名，仅传入当前 SDK 版本支持的参数，
    避免版本差异导致的不兼容问题。

    Returns:
        Dict[str, Any]: 包含可选配置项的字典，可直接解包传入 Config.from_apikey。
    """
    try:
        import inspect
        from longbridge.openapi import Config, Language, PushCandlestickMode
    except Exception:
        return {}

    try:
        params = inspect.signature(Config.from_apikey).parameters
    except Exception:
        return {}

    kw: Dict[str, Any] = {}

    # 控制是否打印行情包详情，默认安静输出（False）
    if "enable_print_quote_packages" in params:
        # 未设置 / 空值 → False（安静输出）；SDK 默认会是详细输出——这里显式选择安静模式。
        raw = os.getenv("LONGBRIDGE_PRINT_QUOTE_PACKAGES")
        if raw is None or not str(raw).strip():
            kw["enable_print_quote_packages"] = False
        else:
            raw_norm = str(raw).strip().lower()
            kw["enable_print_quote_packages"] = raw_norm not in ("0", "false", "no")

    # 从环境变量读取自定义的 HTTP/WebSocket URL
    for pname, envname in (
        ("http_url", "LONGBRIDGE_HTTP_URL"),
        ("quote_ws_url", "LONGBRIDGE_QUOTE_WS_URL"),
        ("trade_ws_url", "LONGBRIDGE_TRADE_WS_URL"),
    ):
        if pname in params:
            v = os.getenv(envname, "").strip()
            if v:
                kw[pname] = v

    # 根据 REPORT_LANGUAGE 环境变量设置 SDK 语言
    if "language" in params:
        try:
            from src.report_language import normalize_report_language

            rl = normalize_report_language(os.getenv("REPORT_LANGUAGE"), default="zh")
            if rl == "zh":
                kw["language"] = Language.ZH_CN
            elif rl == "en":
                kw["language"] = Language.EN
        except Exception as e:
            logger.debug("Longbridge language from REPORT_LANGUAGE skipped: %s", e)

    # 是否启用隔夜行情推送
    if "enable_overnight" in params:
        o = os.getenv("LONGBRIDGE_ENABLE_OVERNIGHT", "").strip().lower()
        if o:
            kw["enable_overnight"] = o in ("1", "true", "yes")

    # K 线推送模式：realtime（实时）或 confirmed（确认后）
    if "push_candlestick_mode" in params:
        cm = os.getenv("LONGBRIDGE_PUSH_CANDLESTICK_MODE", "").strip().lower()
        if cm == "realtime":
            kw["push_candlestick_mode"] = PushCandlestickMode.Realtime
        elif cm == "confirmed":
            kw["push_candlestick_mode"] = PushCandlestickMode.Confirmed
        elif cm:
            logger.warning(
                "Unknown LONGBRIDGE_PUSH_CANDLESTICK_MODE=%r; use realtime or confirmed", cm
            )

    # 设置 SDK 日志文件路径
    if "log_path" in params:
        try:
            log_dir = (os.getenv("LOG_DIR") or "./logs").strip() or "./logs"
            p = Path(log_dir).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            kw["log_path"] = str(p / "longbridge_sdk.log")
        except Exception as e:
            logger.debug("Longbridge log_path from LOG_DIR skipped: %s", e)

    return kw


def _is_us_code(stock_code: str) -> bool:
    """
    判断是否为长桥可报价的美股/美股指数代码。

    Args:
        stock_code (str): 待判断的股票代码。

    Returns:
        bool: 若为美股或美股指数代码则返回 True，否则返回 False。
    """
    normalized = stock_code.strip().upper()
    return is_us_stock_code(normalized) or is_us_index_code(normalized)


def _is_hk_code(stock_code: str) -> bool:
    """
    判断是否为常见港股代码形式，如 HK00700 或 0700.HK。

    支持的格式：
        - HK00700（以 HK 开头，后跟 1-5 位数字）
        - 0700.HK（以 .HK 结尾）
        - 00700（5 位纯数字，默认按港股处理）

    Args:
        stock_code (str): 待判断的股票代码。

    Returns:
        bool: 若为港股代码则返回 True，否则返回 False。
    """
    normalized = (stock_code or "").strip().upper()
    if normalized.startswith("HK"):
        digits = normalized[2:]
        return digits.isdigit() and 1 <= len(digits) <= 5
    if normalized.endswith(".HK"):
        return True
    if normalized.isdigit() and len(normalized) == 5:
        return True
    return False


def _to_longbridge_symbol(stock_code: str) -> Optional[str]:
    """
    将内部股票代码转换为长桥符号格式。

    转换规则：
        - AAPL      -> AAPL.US
        - HK00700   -> 0700.HK
        - 00700     -> 0700.HK（5 位纯数字按港股处理）

    Args:
        stock_code (str): 内部使用的股票代码。

    Returns:
        Optional[str]: 转换后的长桥符号，若无法识别则返回 None。
    """
    code = stock_code.strip()
    upper = code.upper()

    # 若已包含 .US 或 .HK 后缀，直接返回大写形式
    if upper.endswith(".US"):
        return upper
    if upper.endswith(".HK"):
        return upper

    # 判断是否为美股代码
    if _is_us_code(code):
        return f"{upper}.US"

    # 判断是否为港股代码并进行格式化
    if _is_hk_code(code):
        upper = code.upper()
        if upper.startswith("HK"):
            digits = upper[2:]
        else:
            digits = upper
        # 去除前导零，若全为零则保留一个 "0"
        digits = digits.lstrip("0") or "0"
        # 港股代码补零至 4 位（如 700 -> 0700）
        return f"{digits.zfill(4)}.HK"

    return None


class LongbridgeFetcher(BaseFetcher):
    """
    长桥 OpenAPI 数据源实现

    优先级: 5（最低，作为美股/港股最后兜底）
    数据来源: Longbridge OpenAPI

    通过组合多个 API 计算 yfinance 缺失的指标:
    - turnover_rate = volume / circulating_shares * 100
    - volume_ratio = today_volume / avg_5day_volume
    - pe_ratio = price / eps_ttm
    """

    # 数据源名称，用于日志和调试识别
    name = "LongbridgeFetcher"
    # 数据源优先级，数值越小优先级越高；默认 5，可通过环境变量 LONGBRIDGE_PRIORITY 调整
    priority = int(os.getenv("LONGBRIDGE_PRIORITY", "5"))

    # 长桥 SDK 连接生命周期相关的错误关键词，用于识别连接异常
    _CONNECTION_ERRORS = ("client is closed", "context closed", "connection closed")

    def __init__(self):
        """
        初始化懒加载行情上下文、可用性状态与 static_info 缓存。

        属性说明：
            _ctx: 懒加载的 QuoteContext 实例
            _config: 长桥 SDK 配置对象
            _ctx_lock: 线程锁，保证 QuoteContext 懒加载的线程安全
            _available: 缓存的可用性状态（None 表示尚未检测）
            _cooldown_until: 连接冷却期结束时间戳（秒级）
            _static_cache: static_info 进程内缓存字典，格式为 {symbol: (StaticInfo, 时间戳)}
            _static_cache_lock: 保护 _static_cache 的线程锁
        """
        self._ctx = None
        self._config = None
        self._ctx_lock = threading.Lock()
        self._available = None
        self._cooldown_until = 0.0
        # {symbol: (StaticInfo, 时间戳)}
        self._static_cache: Dict[str, Any] = {}
        self._static_cache_lock = threading.Lock()

    def _is_connection_error(self, exc: Exception) -> bool:
        """
        判断是否为长桥 SDK 连接生命周期相关错误。

        Args:
            exc (Exception): 捕获到的异常对象。

        Returns:
            bool: 若为连接相关错误则返回 True，否则返回 False。
        """
        msg = str(exc).lower()
        return any(s in msg for s in self._CONNECTION_ERRORS)

    def _invalidate_ctx(self):
        """
        重置缓存的上下文，使下次调用重建连接。

        通常在检测到连接异常或配置变更时调用。
        """
        with self._ctx_lock:
            self._ctx = None
            self._config = None

    def _mark_connection_cooldown(self, exc: Exception) -> None:
        """
        重置上下文并在冷却期内抑制重连尝试。

        当检测到连接异常时调用，避免在短时间内频繁重连导致服务抖动。

        Args:
            exc (Exception): 触发冷却的异常对象，用于日志记录。
        """
        cooldown_seconds = _connection_cooldown_seconds()
        self._invalidate_ctx()
        if cooldown_seconds <= 0:
            return
        self._cooldown_until = time.time() + cooldown_seconds
        logger.warning(
            "[Longbridge] 检测到连接异常，进入 %ss 冷却期以避免频繁重连: %s",
            cooldown_seconds,
            exc,
        )

    def is_available_for_request(self, capability: str = "") -> bool:
        """
        返回请求时可用性，包含临时冷却状态。

        检查逻辑：
            1. 若数据源未配置（无凭证），返回 False
            2. 若处于冷却期，记录日志并返回 False
            3. 若冷却期已过，重置冷却标记并返回 True

        Args:
            capability (str): 请求的能力标识，用于日志输出，默认为空字符串。

        Returns:
            bool: 若当前可用则返回 True，否则返回 False。
        """
        if not self._is_available():
            return False
        if self._cooldown_until > time.time():
            logger.debug(
                "[Longbridge] %s 冷却中，暂时跳过请求，剩余 %.1fs",
                capability or "request",
                self._cooldown_until - time.time(),
            )
            return False
        if self._cooldown_until:
            self._cooldown_until = 0.0
        return True

    def _is_available(self) -> bool:
        """
        检查是否已配置长桥凭证。

        优先从 src.config.get_config() 读取配置，若失败则回退到环境变量。
        结果会缓存到 self._available 中，避免重复检测。

        Returns:
            bool: 若已配置必要的凭证则返回 True，否则返回 False。
        """
        if self._available is not None:
            return self._available
        try:
            from src.config import get_config
            config = get_config()
            has_creds = bool(
                config.longbridge_app_key
                and config.longbridge_app_secret
                and config.longbridge_access_token
            )
        except Exception:
            has_creds = bool(
                os.getenv("LONGBRIDGE_APP_KEY")
                and os.getenv("LONGBRIDGE_APP_SECRET")
                and os.getenv("LONGBRIDGE_ACCESS_TOKEN")
            )
        self._available = has_creds
        return has_creds

    def _get_ctx(self):
        """
        懒初始化 QuoteContext（线程安全）。

        初始化流程：
            1. 清理空的 URL 环境变量并应用 REGION 映射
            2. 确保凭证已在环境变量中可用
            3. 构造 Config（优先使用 from_apikey_env，回退到 from_apikey）
            4. 创建 QuoteContext 实例

        Returns:
            QuoteContext: 初始化成功的行情上下文，若初始化失败则返回 None。
        """
        if self._ctx is not None:
            return self._ctx
        with self._ctx_lock:
            if self._ctx is not None:
                return self._ctx
            if not self._is_available():
                return None
            try:
                from longbridge.openapi import QuoteContext, Config

                # ── 1. 清理空的 URL 环境变量并应用 REGION 映射 ──
                _sanitize_longbridge_env()

                # ── 2. 确保凭证已在环境变量中可用 ──
                try:
                    from src.config import get_config
                    app_config = get_config()
                    app_key = app_config.longbridge_app_key
                    app_secret = app_config.longbridge_app_secret
                    access_token = app_config.longbridge_access_token
                except Exception:
                    app_key = os.getenv("LONGBRIDGE_APP_KEY")
                    app_secret = os.getenv("LONGBRIDGE_APP_SECRET")
                    access_token = os.getenv("LONGBRIDGE_ACCESS_TOKEN")

                # 将凭证同步到环境变量，供 SDK 读取
                for k, v in {
                    "LONGBRIDGE_APP_KEY": app_key,
                    "LONGBRIDGE_APP_SECRET": app_secret,
                    "LONGBRIDGE_ACCESS_TOKEN": access_token,
                }.items():
                    if v and not os.environ.get(k):
                        os.environ[k] = v

                # ── 3. 构造 Config ──
                extra_kw = _longbridge_config_kwargs()
                lb_config = None

                # 优先使用 from_apikey_env()——它会读取所有 LONGBRIDGE_* 环境变量
                # （凭证 + URL + 选项），包括 .env 文件。该方法在 longbridge >= 4.x 可用，
                # from_env() 仅存在于尚未发布的 master 分支。
                for factory_name in ("from_apikey_env", "from_env"):
                    factory = getattr(Config, factory_name, None)
                    if factory is None:
                        continue
                    try:
                        lb_config = factory()
                        logger.info("[Longbridge] Config.%s() 成功", factory_name)
                        break
                    except Exception as e:
                        logger.debug(
                            "[Longbridge] Config.%s() 失败: %s", factory_name, e
                        )

                # 若 from_apikey_env/from_env 均失败，回退到显式传入凭证的 from_apikey
                if lb_config is None:
                    lb_config = Config.from_apikey(
                        app_key,
                        app_secret,
                        access_token,
                        **extra_kw,
                    )
                    logger.info("[Longbridge] Config.from_apikey() 创建成功")

                # 诊断日志：输出当前使用的区域和 URL 配置
                region = os.getenv("LONGBRIDGE_REGION") or os.getenv("LONGPORT_REGION") or "(auto)"
                logger.info(
                    "[Longbridge] 配置: region=%s, http=%s, quote_ws=%s",
                    region,
                    os.getenv("LONGBRIDGE_HTTP_URL", "(default)"),
                    os.getenv("LONGBRIDGE_QUOTE_WS_URL", "(default)"),
                )

                self._config = lb_config
                self._ctx = QuoteContext(lb_config)
                logger.info("[Longbridge] QuoteContext 初始化成功")
                return self._ctx
            except Exception as e:
                logger.warning("[Longbridge] QuoteContext 初始化失败: %s", e)
                self._available = False
                return None

    # ------------------------------------------------------------------
    # static_info 与缓存
    # ------------------------------------------------------------------

    def _get_static_info(self, symbol: str) -> Optional[Any]:
        """
        拉取 static_info（股本、EPS、BPS、名称），可选进程内 TTL 缓存。

        static_info 包含股票的静态基本面数据，如总股本、流通股本、每股收益等。
        通过进程内缓存减少重复请求，提升性能。

        Args:
            symbol (str): 长桥格式的股票符号（如 "AAPL.US"）。

        Returns:
            Optional[Any]: 包含静态信息的 SDK 对象，若获取失败则返回 None。
        """
        ttl = _static_info_ttl_seconds()
        now = time.time()
        # 若缓存已存在且在有效期内，直接返回缓存值
        if ttl > 0:
            with self._static_cache_lock:
                cached = self._static_cache.get(symbol)
                if cached and (now - cached[1]) < ttl:
                    return cached[0]

        ctx = self._get_ctx()
        if ctx is None:
            return None
        try:
            infos = ctx.static_info([symbol])
            if infos:
                info = infos[0]
                # 将结果存入缓存
                if ttl > 0:
                    with self._static_cache_lock:
                        self._static_cache[symbol] = (info, now)
                return info
        except Exception as e:
            logger.debug(f"[Longbridge] static_info({symbol}) 失败: {e}")
            if self._is_connection_error(e):
                self._mark_connection_cooldown(e)
        return None

    # ------------------------------------------------------------------
    # 通过 static_info 获取股票名称
    # ------------------------------------------------------------------

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """
        从长桥 static_info 返回股票名称（name_cn 或 name_en）。

        Args:
            stock_code (str): 内部使用的股票代码。

        Returns:
            Optional[str]: 股票中文或英文名称，若获取失败则返回 None。
        """
        symbol = _to_longbridge_symbol(stock_code)
        if symbol is None:
            return None
        info = self._get_static_info(symbol)
        if info is None:
            return None
        name = getattr(info, "name_cn", "") or getattr(info, "name_en", "") or ""
        return name.strip() or None

    # ------------------------------------------------------------------
    # 基于历史数据计算量比
    # ------------------------------------------------------------------

    def _ts_sort_key(self, candle: Any) -> float:
        """
        为 K 线时间戳生成单调排序键（UTC 秒或 datetime）。

        Args:
            candle: 包含 timestamp 属性的 K 线对象。

        Returns:
            float: 可用于排序的浮点数时间戳。
        """
        ts = getattr(candle, "timestamp", None)
        if ts is None:
            return 0.0
        if hasattr(ts, "timestamp"):
            return float(ts.timestamp())
        return float(int(ts))

    def _compute_volume_ratio(self, symbol: str, today_volume: int) -> Optional[float]:
        """
        计算量比 = 当日成交量 / 近期已完成日成交量均值。

        算法说明：
            以最近一根日 K 线作为「当日/未结束」的参考窗口，取其之前 5 根日 K 线的成交量均值。
            避免用本地 `date.today()` 做日期匹配——当进程运行在 CN 时区时，这种方式对美股代码会失效。

        Args:
            symbol (str): 长桥格式的股票符号。
            today_volume (int): 当日成交量。

        Returns:
            Optional[float]: 计算得到的量比值（保留两位小数），若数据不足则返回 None。
        """
        if not today_volume or today_volume <= 0:
            return None
        ctx = self._get_ctx()
        if ctx is None:
            return None
        try:
            from longbridge.openapi import Period, AdjustType

            # 获取最近 6 根日 K 线（含当日/最近一根）
            candles = ctx.history_candlesticks_by_offset(
                symbol,
                Period.Day,
                AdjustType.NoAdjust,
                False,
                6,
                datetime.now(),
            )
            if not candles or len(candles) < 2:
                return None

            # 按时间戳降序排列，取最近一根作为当日，其余作为历史
            ordered = sorted(candles, key=self._ts_sort_key, reverse=True)
            past_vols: list = []
            for c in ordered[1:6]:
                vol = int(getattr(c, "volume", 0) or 0)
                if vol > 0:
                    past_vols.append(vol)

            if not past_vols:
                return None

            # 计算历史平均成交量
            avg_vol = sum(past_vols) / len(past_vols)
            if avg_vol <= 0:
                return None

            return round(today_volume / avg_vol, 2)
        except Exception as e:
            logger.debug(f"[Longbridge] 计算量比失败({symbol}): {e}")
            return None

    # ------------------------------------------------------------------
    # 获取实时行情
    # ------------------------------------------------------------------

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """
        从长桥获取实时行情，并计算衍生字段。

        获取流程：
            1. 检查数据源可用性（含冷却期判断）
            2. 转换股票代码为长桥符号格式
            3. 调用 quote API 获取实时行情
            4. 计算涨跌幅、振幅等衍生指标
            5. 拉取 static_info 计算换手率、市盈率、市净率、市值等
            6. 计算量比

        Args:
            stock_code (str): 内部使用的股票代码。

        Returns:
            Optional[UnifiedRealtimeQuote]: 统一的实时行情对象，若获取失败则返回 None。
        """
        if not self.is_available_for_request("realtime_quote"):
            return None

        symbol = _to_longbridge_symbol(stock_code)
        if symbol is None:
            logger.debug(f"[Longbridge] 无法转换代码: {stock_code}")
            return None

        ctx = self._get_ctx()
        if ctx is None:
            return None

        try:
            quotes = ctx.quote([symbol])
            if not quotes:
                return None
            q = quotes[0]
        except Exception as e:
            logger.info(f"[Longbridge] quote({symbol}) 失败: {e}")
            if self._is_connection_error(e):
                self._mark_connection_cooldown(e)
            return None

        # 提取实时行情基础字段
        price = safe_float(getattr(q, "last_done", None))
        if price is None or price <= 0:
            return None

        prev_close = safe_float(getattr(q, "prev_close", None))
        open_price = safe_float(getattr(q, "open", None))
        high = safe_float(getattr(q, "high", None))
        low = safe_float(getattr(q, "low", None))
        volume = int(getattr(q, "volume", 0) or 0)
        turnover = safe_float(getattr(q, "turnover", None))

        # 计算涨跌幅、振幅等衍生指标
        change_amount = None
        change_pct = None
        amplitude = None
        if prev_close and prev_close > 0:
            change_amount = round(price - prev_close, 4)
            change_pct = round((price - prev_close) / prev_close * 100, 2)
            if high is not None and low is not None:
                amplitude = round((high - low) / prev_close * 100, 2)

        # 拉取 static_info 以计算衍生字段
        static = self._get_static_info(symbol)

        turnover_rate = None
        pe_ratio = None
        pb_ratio = None
        total_mv = None
        circ_mv = None
        name = ""

        if static is not None:
            name = getattr(static, "name_cn", "") or getattr(static, "name_en", "") or ""
            circulating = int(getattr(static, "circulating_shares", 0) or 0)
            total_shares = int(getattr(static, "total_shares", 0) or 0)
            eps_ttm = safe_float(getattr(static, "eps_ttm", None))
            eps_plain = safe_float(getattr(static, "eps", None))
            bps = safe_float(getattr(static, "bps", None))

            # 计算换手率：
            # 美股代码常报告 circulating_shares=0 而 total_shares 有值——计算换手率时改用总股本。
            shares_for_turnover = circulating if circulating > 0 else total_shares
            if shares_for_turnover > 0 and volume > 0:
                turnover_rate = round(volume / shares_for_turnover * 100, 4)
            elif volume > 0:
                logger.debug(
                    "[Longbridge] %s 无法计算换手率: volume=%s circulating=%s total_shares=%s",
                    symbol,
                    volume,
                    circulating,
                    total_shares,
                )

            # 计算市盈率（PE）：优先使用 eps_ttm，若不存在则回退到 eps_plain
            eps_for_pe = None
            if eps_ttm is not None and eps_ttm > 0:
                eps_for_pe = eps_ttm
            elif eps_plain is not None and eps_plain > 0:
                eps_for_pe = eps_plain
            if eps_for_pe:
                pe_ratio = round(price / eps_for_pe, 2)

            # 计算市净率（PB）
            if bps is not None and bps > 0:
                pb_ratio = round(price / bps, 2)
            # 计算总市值和流通市值
            if total_shares > 0:
                total_mv = round(price * total_shares, 2)
            if circulating > 0:
                circ_mv = round(price * circulating, 2)

        # 计算量比
        volume_ratio = self._compute_volume_ratio(symbol, volume)

        # 构造统一的实时行情对象
        quote = UnifiedRealtimeQuote(
            code=stock_code,
            name=name,
            source=RealtimeSource.LONGBRIDGE,
            price=price,
            change_pct=change_pct,
            change_amount=change_amount,
            volume=volume if volume > 0 else None,
            amount=turnover,
            volume_ratio=volume_ratio,
            turnover_rate=turnover_rate,
            amplitude=amplitude,
            open_price=open_price,
            high=high,
            low=low,
            pre_close=prev_close,
            pe_ratio=pe_ratio,
            pb_ratio=pb_ratio,
            total_mv=total_mv,
            circ_mv=circ_mv,
        )

        logger.info(
            f"[Longbridge] {symbol} 行情获取成功: "
            f"价格={price}, 量比={volume_ratio}, 换手率={turnover_rate}"
        )
        return quote

    # ------------------------------------------------------------------
    # BaseFetcher 抽象方法（历史日线数据）
    # ------------------------------------------------------------------

    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """
        从长桥获取历史 K 线数据。

        调用 longbridge.openapi 的 history_candlesticks_by_date 接口，
        获取指定日期范围内的前复权日 K 线数据。

        Args:
            stock_code (str): 内部使用的股票代码。
            start_date (str): 开始日期，格式 "YYYY-MM-DD"。
            end_date (str): 结束日期，格式 "YYYY-MM-DD"。

        Returns:
            pd.DataFrame: 包含历史 K 线数据的 DataFrame，若获取失败则返回空 DataFrame。

        Raises:
            RuntimeError: 当长桥数据源暂时不可用时抛出。
            ValueError: 当股票代码无法转换为长桥符号时抛出。
        """
        if not self.is_available_for_request("daily_data"):
            raise RuntimeError("Longbridge temporarily unavailable for daily_data")

        symbol = _to_longbridge_symbol(stock_code)
        if symbol is None:
            raise ValueError(f"Cannot convert {stock_code} to Longbridge symbol")

        ctx = self._get_ctx()
        if ctx is None:
            raise RuntimeError("Longbridge QuoteContext not available")

        from longbridge.openapi import Period, AdjustType

        # 将字符串日期解析为 date 对象
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

        try:
            candles = ctx.history_candlesticks_by_date(
                symbol,
                Period.Day,
                AdjustType.ForwardAdjust,
                start_dt,
                end_dt,
            )
        except Exception as e:
            if self._is_connection_error(e):
                self._mark_connection_cooldown(e)
            raise

        if not candles:
            return pd.DataFrame()

        # 将 K 线数据转换为 DataFrame 行
        rows = []
        for c in candles:
            ts = getattr(c, "timestamp", None)
            if ts is None:
                continue
            if hasattr(ts, "date"):
                dt = ts.date()
            else:
                dt = datetime.fromtimestamp(int(ts)).date()

            rows.append({
                "date": dt.strftime("%Y-%m-%d"),
                "open": safe_float(getattr(c, "open", None)),
                "high": safe_float(getattr(c, "high", None)),
                "low": safe_float(getattr(c, "low", None)),
                "close": safe_float(getattr(c, "close", None)),
                "volume": int(getattr(c, "volume", 0) or 0),
                "turnover": safe_float(getattr(c, "turnover", None)),
            })

        return pd.DataFrame(rows)

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        将列名标准化为标准格式。

        标准化规则：
            1. 将 "turnover" 重命名为 "amount"
            2. 若不存在 "pct_chg" 列且存在 "close" 列，则计算日涨跌幅百分比
            3. 确保所有标准列都存在，缺失的列填充为 None
            4. 按标准列顺序返回

        Args:
            df (pd.DataFrame): 原始历史数据 DataFrame。
            stock_code (str): 股票代码（当前未使用，保留接口一致性）。

        Returns:
            pd.DataFrame: 标准化后的 DataFrame，列名和顺序符合 STANDARD_COLUMNS 定义。
        """
        if df.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        # 将 turnover 列重命名为 amount，统一命名规范
        rename_map = {"turnover": "amount"}
        df = df.rename(columns=rename_map)

        # 若不存在 pct_chg 列，则基于 close 列计算日涨跌幅百分比
        if "pct_chg" not in df.columns and "close" in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100

        # 确保所有标准列都存在，缺失的列填充为 None
        for col in STANDARD_COLUMNS:
            if col not in df.columns:
                df[col] = None

        # 按标准列顺序返回，保证输出格式一致
        return df[STANDARD_COLUMNS]
