# -*- coding: utf-8 -*-
"""从 Futu OpenD 实例读取真实股票持仓。

本模块在分析启动时被调用以加载「自选股组合」中的真实账户持仓：
通过 Futu OpenAPI SDK 连接本地 OpenD 网关，发现处于 ACTIVE 状态的
REAL NORMAL/MASTER 账户，取非零多头持仓并把证券代码转换为分析流水
线约定的 A/HK/US 代码格式，供后续行情抓取与策略分析使用。

主要能力：
- 配置读取与 SDK 加载（含缺失/网络环境受限的友好错误）
- 真实账户发现（REAL + ACTIVE + NORMAL/MASTER），按需按 ``FUTU_ACC_ID`` 筛选
- 多账户 LONG 持仓聚合、去重，并跳过 SHORT 与非正股
- 港/A/美 代码规范化到分析代码格式
"""

from __future__ import annotations

import ipaddress
import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

from data_provider.us_index_mapping import is_us_stock_code
from src.services.stock_code_utils import normalize_code


logger = logging.getLogger(__name__)


class FutuPortfolioError(RuntimeError):
    """在无法安全解析 Futu 持仓时抛出，统一对外的错误类型。

    覆盖 SDK 未安装、OpenD 不可达、账户状态异常、返回数据不合法
    等所有需要中断 CLI 调用方的失败路径，便于上层做一致错误处理。
    """


@dataclass(frozen=True)
class _FutuAccount:
    """表示一个可用的真实 Futu 证券账户。

    Attributes:
        acc_id: Futu 真实账户 ID（正整数）。
        security_firm: 该账户实际所属的券商枚举值，由账户列表查询返回。
    """

    acc_id: int
    security_firm: Any


@dataclass(frozen=True)
class _FutuApi:
    """封装持仓加载所需的 Futu SDK 表面，便于在测试中替换/打桩。

    Attributes:
        OpenQuoteContext: 行情上下文类，用于查询证券基础信息等。
        OpenSecTradeContext: 交易上下文类，用于查询账户/持仓。
        Market: 行情市场枚举。
        RET_OK: SDK 通用成功返回码。
        SecurityFirm: 券商枚举。
        SecurityType: 证券类型枚举。
        TrdEnv: 交易环境枚举（REAL/SIMULATE）。
        TrdMarket: 交易标的所在市场枚举。
    """

    OpenQuoteContext: Any
    OpenSecTradeContext: Any
    Market: Any
    RET_OK: Any
    SecurityFirm: Any
    SecurityType: Any
    TrdEnv: Any
    TrdMarket: Any


# 仅纳入常规交易账户与主账户；模拟/只读账户不在分析范围内。
_SUPPORTED_ACCOUNT_ROLES = frozenset({"NORMAL", "MASTER"})
# 分析流水线仅覆盖港/A/美股票；其他 Futu 市场的代码将在过滤阶段跳过。
_SUPPORTED_ANALYSIS_MARKETS = frozenset({"US", "HK", "SH", "SZ"})
# 这些枚举值表示 Futu 返回的证券类型无效或未知，应忽略而非视为正股。
_UNKNOWN_SECURITY_TYPES = frozenset({"", "N/A", "NONE", "UNKNOWN", "NAN"})
# ``get_stock_basicinfo`` 单次允许的最大代码数；超出会按批次切片循环查询。
_STATIC_INFO_BATCH_SIZE = 100


def _load_futu_api() -> _FutuApi:
    """加载持仓加载所需的 Futu SDK 表面；失败时抛出可操作错误。

    - ``ImportError`` 单独捕获并提示先执行 ``uv sync --locked`` 安装 SDK。
    - 其他异常通常源于 SDK 导入时初始化文件日志器失败，按通用异常捕获并翻译。
    """

    try:
        from futu import (
            Market,
            OpenQuoteContext,
            OpenSecTradeContext,
            RET_OK,
            SecurityFirm,
            SecurityType,
            TrdEnv,
            TrdMarket,
        )
    except ImportError as exc:
        raise FutuPortfolioError(
            "未安装 Futu OpenAPI SDK；请先执行 `uv sync --locked`。"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - SDK import initializes its file logger
        raise FutuPortfolioError(f"加载 Futu OpenAPI SDK 失败: {exc}") from exc

    return _FutuApi(
        OpenQuoteContext=OpenQuoteContext,
        OpenSecTradeContext=OpenSecTradeContext,
        Market=Market,
        RET_OK=RET_OK,
        SecurityFirm=SecurityFirm,
        SecurityType=SecurityType,
        TrdEnv=TrdEnv,
        TrdMarket=TrdMarket,
    )


def _enum_text(value: Any) -> str:
    """将 SDK 枚举类值规范化为可比较的大写字符串。

    优先读取枚举的 ``.name``，否则回退到 ``str(value)``；统一去除空白
    并转为大写，便于与硬编码字符串（"REAL"、"STOCK" 等）做稳定比较。
    """

    if value is None:
        return ""
    name = getattr(value, "name", None)
    return str(name if name is not None else value).strip().upper()


def _iter_rows(data: Any, operation: str) -> Iterable[Any]:
    """遍历账户/持仓接口返回的 pandas 风格表格，逐行产出。

    Args:
        data: SDK 返回的 DataFrame 形式表格。
        operation: 调用方名称，用于在 ``data`` 不是表格时构造可定位的错误消息。

    Returns:
        形如 ``(index, row) -> row`` 的行生成器。

    Raises:
        FutuPortfolioError: 当 ``data`` 不具备 ``iterrows`` 方法时抛出。
    """

    iterrows = getattr(data, "iterrows", None)
    if not callable(iterrows):
        raise FutuPortfolioError(f"{operation}返回了非表格数据")
    return (row for _, row in iterrows())


def _safe_close(context: Any) -> None:
    """尽力关闭 SDK 上下文，不掩盖主操作的成功/失败。

    调用方通常把 context 关闭放在 ``finally`` 中，并吞掉关闭异常：
    - 主操作异常优先向外抛；
    - 关闭失败仅记录 debug 日志，便于排查但不影响主结果。
    """

    if context is None:
        return
    try:
        context.close()
    except Exception:  # pragma: no cover - closing is best effort
        logger.debug("关闭 Futu OpenD 连接失败", exc_info=True)


def _connection_settings() -> tuple[str, int]:
    """从环境变量读取并校验 OpenD 的 IPv4 主机与端口。

    Returns:
        ``(host, port)`` 元组。``host`` 保留原始字符串（可含方括号）；
        ``port`` 是经过 ``1 <= port <= 65535`` 校验的合法端口号。

    Raises:
        FutuPortfolioError: 端口非整数、超出范围，或主机解析为非 IPv4。
    """

    host = (os.getenv("FUTU_OPEND_HOST") or "127.0.0.1").strip()
    raw_port = (os.getenv("FUTU_OPEND_PORT") or "11111").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise FutuPortfolioError(f"FUTU_OPEND_PORT 不是有效端口: {raw_port!r}") from exc
    if not host or not 1 <= port <= 65535:
        raise FutuPortfolioError(f"Futu OpenD 地址无效: {host!r}:{port}")

    address_text = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        address = None
    if address is not None and address.version != 4:
        raise FutuPortfolioError(
            "futu-api==10.8.6808 的网络层仅支持 IPv4；"
            f"FUTU_OPEND_HOST 当前为 {host!r}，请改用 IPv4 地址或可解析到 IPv4 的主机名。"
        )
    return host, port


def _configured_account_id() -> Optional[int]:
    """读取可选的 ``FUTU_ACC_ID``，并校验其为正整数。

    Returns:
        未配置时返回 ``None``；配置了则返回正整数账户 ID。

    Raises:
        FutuPortfolioError: 环境变量不是整数或不大于 0。
    """

    value = (os.getenv("FUTU_ACC_ID") or "").strip()
    if not value:
        return None
    try:
        account_id = int(value)
    except ValueError as exc:
        raise FutuPortfolioError("FUTU_ACC_ID 必须是正整数账户 ID") from exc
    if account_id <= 0:
        raise FutuPortfolioError("FUTU_ACC_ID 必须是正整数账户 ID")
    return account_id


def _configured_security_firm(api: _FutuApi) -> Any:
    """解析 ``FUTU_SECURITY_FIRM``，未配置时使用 SDK 的自动探测模式。

    Args:
        api: 已加载的 Futu SDK 表面，用于按名称查找 ``SecurityFirm`` 枚举。

    Returns:
        对应券商枚举；默认 ``NONE`` 即 SDK 自动探测。

    Raises:
        FutuPortfolioError: 配置了 SDK 不支持的券商名。
    """

    name = (os.getenv("FUTU_SECURITY_FIRM") or "NONE").strip().upper()
    firm = getattr(api.SecurityFirm, name, None)
    if firm is None:
        raise FutuPortfolioError(f"不支持的 FUTU_SECURITY_FIRM: {name}")
    return firm


def _discover_real_accounts(api: _FutuApi, host: str, port: int) -> List[_FutuAccount]:
    """发现当前 OpenD 下处于 ACTIVE 状态的 REAL 普通/主账户。

    仅纳入 ``trd_env == REAL`` 且 ``acc_status == ACTIVE`` 且 ``acc_role``
    在 ``_SUPPORTED_ACCOUNT_ROLES`` 集合内的账户，并按 ``acc_id`` 去重。
    若配置了 ``FUTU_ACC_ID``，则进一步在结果中按该 ID 精确筛选；找不到
    或账户 ID 非法时抛出 ``FutuPortfolioError``，便于 CLI 调用方排错。

    Args:
        api: 已加载的 Futu SDK 表面。
        host: OpenD 主机。
        port: OpenD 端口。

    Returns:
        满足筛选条件的 :class:`_FutuAccount` 列表。
    """

    accounts: List[_FutuAccount] = []
    seen_ids = set()
    requested_acc_id = _configured_account_id()
    security_firm = _configured_security_firm(api)
    context = None
    try:
        context = api.OpenSecTradeContext(
            host=host,
            port=port,
            # ``TrdMarket.NONE`` 表示不限定市场，由后续按账户 acc_id 自然分布。
            filter_trdmarket=api.TrdMarket.NONE,
            security_firm=security_firm,
        )
        ret, data = context.get_acc_list()
        if ret != api.RET_OK:
            raise FutuPortfolioError(f"查询 Futu 真实账户失败: {data}")
        for row in _iter_rows(data, "Futu 账户查询"):
            if _enum_text(row.get("trd_env")) != "REAL":
                continue
            if _enum_text(row.get("acc_status")) != "ACTIVE":
                continue
            if _enum_text(row.get("acc_role")) not in _SUPPORTED_ACCOUNT_ROLES:
                continue
            raw_acc_id = row.get("acc_id")
            try:
                acc_id = int(raw_acc_id)
                # 仅当原始字段是字符串，或数值本身等于 ``int(raw_acc_id)`` 时视为整数。
                exact_integer = isinstance(raw_acc_id, str) or bool(
                    raw_acc_id == acc_id
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise FutuPortfolioError(
                    "Futu 账户查询返回了无效账户 ID"
                ) from exc
            # ``bool`` 是 ``int`` 子类但语义上不是账户 ID，必须排除。
            if isinstance(raw_acc_id, bool) or not exact_integer or acc_id <= 0:
                raise FutuPortfolioError("Futu 账户查询返回了无效账户 ID")
            if acc_id in seen_ids:
                continue
            returned_firm_name = _enum_text(row.get("security_firm"))
            returned_firm = getattr(
                api.SecurityFirm,
                returned_firm_name,
                security_firm,
            )
            seen_ids.add(acc_id)
            accounts.append(_FutuAccount(acc_id=acc_id, security_firm=returned_firm))
    except FutuPortfolioError:
        raise
    except Exception as exc:  # noqa: BLE001 - translate SDK/network failures
        raise FutuPortfolioError(f"查询 Futu 真实账户失败: {exc}") from exc
    finally:
        _safe_close(context)

    if requested_acc_id is not None:
        # 当用户显式指定 ``FUTU_ACC_ID`` 时，把结果收敛到该单一账户。
        accounts = [account for account in accounts if account.acc_id == requested_acc_id]
        if not accounts:
            raise FutuPortfolioError(
                "FUTU_ACC_ID 未匹配到可用的真实证券账户；请检查账户 ID、券商和 OpenD 登录状态。"
            )

    if not accounts:
        raise FutuPortfolioError(
            "未找到状态为 ACTIVE 的 Futu REAL 普通或 MASTER 证券账户"
        )
    return accounts


def _load_position_codes(
    api: _FutuApi,
    host: str,
    port: int,
    accounts: Iterable[_FutuAccount],
) -> List[str]:
    """从给定账户拉取非零多头持仓的证券代码，并在多账户间去重。

    SHORT 持仓与未知方向的持仓会跳过并记录到日志；其余行要求
    ``qty`` 是有限非零数值、``code`` 是形如 ``MARKET.SYMBOL`` 的字符串。
    跨账户同代码只保留一次，便于后续按市场批量查询证券类型。

    Args:
        api: 已加载的 Futu SDK 表面。
        host: OpenD 主机。
        port: OpenD 端口。
        accounts: 已发现的真实账户列表。

    Returns:
        去重后的合法持仓代码列表（仍带 Futu 市场前缀，如 ``HK.00700``）。

    Raises:
        FutuPortfolioError: 持仓查询失败或返回字段不合法。
    """

    codes: List[str] = []
    seen_codes = set()
    skipped_short_count = 0
    skipped_unknown_side_count = 0

    for account in accounts:
        context = None
        try:
            context = api.OpenSecTradeContext(
                host=host,
                port=port,
                filter_trdmarket=api.TrdMarket.NONE,
                security_firm=account.security_firm,
            )
            ret, data = context.position_list_query(
                trd_env=api.TrdEnv.REAL,
                acc_id=account.acc_id,
                # 强制刷新缓存，确保读到的是 OpenD 当前最新持仓而非本地缓存。
                refresh_cache=True,
            )
            if ret != api.RET_OK:
                raise FutuPortfolioError(f"查询 Futu 真实持仓失败: {data}")
            for row in _iter_rows(data, "Futu 持仓查询"):
                position_side = _enum_text(row.get("position_side"))
                # 仅纳入 LONG；SHORT/未识别方向均跳过（融资融券账户常有 SHORT）。
                if position_side == "SHORT":
                    skipped_short_count += 1
                    continue
                if position_side != "LONG":
                    skipped_unknown_side_count += 1
                    continue
                raw_code = row.get("code")
                code = (
                    raw_code.strip().upper()
                    if isinstance(raw_code, str)
                    else ""
                )
                raw_quantity = row.get("qty")
                try:
                    # ``bool`` 是 ``int`` 子类会被 ``float`` 接受，必须先排除。
                    if isinstance(raw_quantity, bool):
                        raise TypeError("boolean quantity")
                    quantity = float(raw_quantity)
                except (TypeError, ValueError) as exc:
                    suffix = f": {code}" if code else ""
                    raise FutuPortfolioError(f"Futu 持仓数量无效{suffix}") from exc
                # 过滤 NaN/Inf 等非有限浮点，避免后续数学运算出现异常。
                if not math.isfinite(quantity):
                    suffix = f": {code}" if code else ""
                    raise FutuPortfolioError(f"Futu 持仓数量无效{suffix}")
                if quantity == 0:
                    continue
                if not isinstance(raw_code, str):
                    raise FutuPortfolioError("Futu 非零持仓返回了无效证券代码")
                if not code:
                    raise FutuPortfolioError("Futu 非零持仓返回了空证券代码")
                market, separator, symbol = code.partition(".")
                # ``partition`` 找不到 ``.`` 时 separator 为空；要求同时存在市场和代码段。
                if not separator or not market or not symbol:
                    raise FutuPortfolioError(
                        f"Futu 非零持仓返回了无效证券代码: {code}"
                    )
                if code in seen_codes:
                    continue
                seen_codes.add(code)
                codes.append(code)
        except FutuPortfolioError:
            raise
        except Exception as exc:  # noqa: BLE001 - translate SDK/network errors for CLI callers
            raise FutuPortfolioError(f"查询 Futu 真实持仓失败: {exc}") from exc
        finally:
            _safe_close(context)

    if skipped_short_count:
        logger.info("已跳过 %d 个 Futu SHORT 空头持仓", skipped_short_count)
    if skipped_unknown_side_count:
        logger.warning(
            "已跳过 %d 个持仓方向不是 LONG 的 Futu 持仓",
            skipped_unknown_side_count,
        )
    return codes


def _market_prefix(code: str) -> str:
    """从形如 ``MARKET.SYMBOL`` 的代码中提取 Futu 市场前缀。

    无 ``.`` 时返回空串，调用方据此判断该代码是否带有市场前缀。
    """
    return code.split(".", 1)[0] if "." in code else ""


def _is_cn_b_share_code(code: str) -> bool:
    """判断给定 Futu 代码是否为沪深 B 股（分析流水线暂不支持）。

    B 股代码规则：上交所 ``SH900xxx``、深交所 ``SZ200xxx``；其余代码返回 False。
    """
    prefix, separator, symbol = code.partition(".")
    if not separator or not (symbol.isdigit() and len(symbol) == 6):
        return False
    return (prefix == "SH" and symbol.startswith("900")) or (
        prefix == "SZ" and symbol.startswith("200")
    )


def _to_analysis_code(futu_code: str) -> Optional[str]:
    """把支持的 Futu 代码转换为分析流水线使用的代码格式。

    - 美股 ``US.AAPL`` → ``AAPL``（须经 ``is_us_stock_code`` 二次确认）。
    - 港股 ``HK.00700`` → ``HK00700``（左 0 补齐 5 位）。
    - 沪深 ``SH600519``/``SZ000001`` → 调用 ``normalize_code`` 标准化。

    Args:
        futu_code: Futu 风格的带市场前缀代码。

    Returns:
        分析代码；无法转换时返回 ``None``。
    """

    prefix, separator, symbol = futu_code.partition(".")
    if not separator or not symbol:
        return None
    prefix = prefix.upper()
    symbol = symbol.upper()
    if prefix == "US":
        normalized = normalize_code(symbol)
        # 只有 ``normalize_code`` 接受且确实是美股代码时才返回，避免误把港/A股当成美股。
        if normalized == symbol and is_us_stock_code(normalized):
            return normalized
        return None
    if prefix == "HK":
        # 港股代码是 1-5 位数字；``zfill(5)`` 统一补齐前导零。
        if not symbol.isdigit() or not 1 <= len(symbol) <= 5:
            return None
        return f"HK{symbol.zfill(5)}"
    if prefix in {"SH", "SZ"}:
        normalized = normalize_code(f"{prefix}{symbol}")
        # ``normalize_code`` 返回值必须与去掉前缀的 symbol 一致，否则视为不规范。
        return normalized if normalized == symbol else None
    return None


def _filter_stock_codes(
    api: _FutuApi,
    host: str,
    port: int,
    position_codes: List[str],
) -> List[str]:
    """保留 A/HK/US 正股代码，跳过其他市场与无法确认证券类型的代码。

    对持仓代码按市场前缀分组，对每个市场批量调用
    ``get_stock_basicinfo``（按 ``_STATIC_INFO_BATCH_SIZE`` 切片）确认
    ``stock_type == STOCK``；无法识别的代码会被收集起来，最终抛出明确错误，
    以避免把 ETF/期权/涡轮等非正股混入分析流程。

    Args:
        api: 已加载的 Futu SDK 表面。
        host: OpenD 主机。
        port: OpenD 端口。
        position_codes: 由 :func:`_load_position_codes` 输出的持仓代码列表。

    Returns:
        转换后的分析代码列表，已去重并保持 ``position_codes`` 中的相对顺序。

    Raises:
        FutuPortfolioError: 行情查询失败，或存在无法确认证券类型/无法转换的代码。
    """

    if not position_codes:
        return []

    grouped: dict[str, List[str]] = {}
    unsupported_codes: List[str] = []
    for code in position_codes:
        prefix = _market_prefix(code)
        # 不在支持市场内、或属于沪深 B 股，统一视为「当前分析流程不支持」。
        if prefix not in _SUPPORTED_ANALYSIS_MARKETS or _is_cn_b_share_code(code):
            unsupported_codes.append(code)
            continue
        grouped.setdefault(prefix, []).append(code)

    if not grouped:
        logger.warning(
            "已跳过 %d 个当前分析流程不支持的 Futu 持仓: %s",
            len(unsupported_codes),
            ", ".join(unsupported_codes),
        )
        return []

    stock_codes = set()
    classified_codes = set()
    context = None
    try:
        context = api.OpenQuoteContext(host=host, port=port)
        for prefix, codes in grouped.items():
            market = getattr(api.Market, prefix, None)
            if market is None:
                unsupported_codes.extend(codes)
                continue
            # 单次查询存在代码数量上限，按批次切片避免触发 SDK 报错。
            for start in range(0, len(codes), _STATIC_INFO_BATCH_SIZE):
                batch = codes[start : start + _STATIC_INFO_BATCH_SIZE]
                ret, data = context.get_stock_basicinfo(
                    market,
                    stock_type=api.SecurityType.STOCK,
                    code_list=batch,
                )
                if ret != api.RET_OK:
                    raise FutuPortfolioError(
                        f"查询 Futu 持仓证券类型失败（{prefix}）: {data}"
                    )
                for row in _iter_rows(data, "Futu 证券类型查询"):
                    code = str(row.get("code", "") or "").strip().upper()
                    if not code:
                        continue
                    stock_type = _enum_text(row.get("stock_type"))
                    if stock_type in _UNKNOWN_SECURITY_TYPES:
                        continue
                    classified_codes.add(code)
                    if stock_type == "STOCK":
                        stock_codes.add(code)
    except FutuPortfolioError:
        raise
    except Exception as exc:  # noqa: BLE001 - translate SDK/network errors for CLI callers
        raise FutuPortfolioError(f"查询 Futu 持仓证券类型失败: {exc}") from exc
    finally:
        _safe_close(context)

    # 找出 SDK 完全未返回的代码，意味着无法判断其证券类型，必须报错而非静默丢弃。
    missing_codes = [
        code
        for codes in grouped.values()
        for code in codes
        if code not in classified_codes
    ]
    if unsupported_codes:
        logger.warning(
            "已跳过 %d 个当前分析流程不支持的 Futu 持仓: %s",
            len(unsupported_codes),
            ", ".join(unsupported_codes),
        )
    if missing_codes:
        raise FutuPortfolioError(
            "无法确认证券类型的 Futu 持仓: " + ", ".join(missing_codes)
        )

    result: List[str] = []
    conversion_failures: List[str] = []
    for futu_code in position_codes:
        # 保留 ``position_codes`` 中的相对顺序，便于日志与上游期望一致。
        if futu_code not in stock_codes:
            continue
        analysis_code = _to_analysis_code(futu_code)
        if not analysis_code:
            conversion_failures.append(futu_code)
            continue
        if analysis_code not in result:
            result.append(analysis_code)
    if conversion_failures:
        raise FutuPortfolioError(
            "无法转换已确认的 Futu 正股代码到当前分析格式: "
            + ", ".join(conversion_failures)
        )
    return result


def load_futu_stock_codes() -> List[str]:
    """从所有选中的 Futu 真实账户返回去重后的分析代码。

    仅保留状态为 ACTIVE 的真实账户，以及数量非零的 Futu ``SecurityType.STOCK``
    LONG（多头）持仓。``FUTU_ACC_ID`` 可选择单个账户；否则合并 NORMAL 与 MASTER
    账户。``MASTER`` 是账户角色，而“只读”描述的是本集成仅做查询类 API 调用的
    特性。券商发现默认使用 SDK 的 ``SecurityFirm.NONE`` 自动探测，除非显式设置了
    ``FUTU_SECURITY_FIRM``。持仓数据始终强制刷新。代码转换仅支持 A/HK/US 股票，
    其他 Futu 市场的持仓会在日志中记录代码并跳过。
    """
    api = _load_futu_api()
    host, port = _connection_settings()
    accounts = _discover_real_accounts(api, host, port)
    position_codes = _load_position_codes(api, host, port, accounts)
    stock_codes = _filter_stock_codes(api, host, port, position_codes)
    logger.info(
        "已从 Futu 真实账户加载 %d 只正股（账户数: %d，原始非零多头持仓数: %d）: %s",
        len(stock_codes),
        len(accounts),
        len(position_codes),
        ", ".join(stock_codes),
    )
    return stock_codes
