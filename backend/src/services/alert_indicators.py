# -*- coding: utf-8 -*-
"""技术指标（indicator）告警助手。

面向 :class:`AlertService` 中 **P5（技术指标）规则** 提供的纯计算型工具：

* 支持五类指标：MA 价穿 / RSI 阈值 / MACD 金叉死叉 / KDJ 金叉死叉 / CCI 阈值。
* 全部输入都先经过 ``normalize_indicator_parameters`` 标准化，保证入库前
  ``period``、``window``、``threshold``、``direction`` 等参数合法且所需
  K 线数量不超过 :data:`MAX_REQUESTED_DAYS`。
* 日 K 线由调用方传入 ``df``（``pd.DataFrame``），本模块不会触发任何外部 IO，
  只负责把 ``df`` 标准化为 :func:`normalize_ohlcv` 描述的"日期 + 必需列"结构，
  然后按指标计算"上一根 → 当前根"的边沿触发（edge-trigger）。
* 返回值统一是 :class:`IndicatorEvaluation`，``status`` 取值:

  - ``triggered``: 指标在最近一根满足触发条件；
  - ``not_triggered``: 数据齐全但未触发；
  - ``degraded``: 数据缺失或不合法导致无法判定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from math import isfinite
from typing import Any, Dict, Optional

import pandas as pd


TECHNICAL_ALERT_TYPES = frozenset({
    # 收盘价对 N 日均线（窗口由用户配置）的穿越/跌破。
    "ma_price_cross",
    # Wilder RSI 越过用户阈值（上下双侧）。
    "rsi_threshold",
    # MACD 中 DIF 与 DEA 的金叉/死叉。
    "macd_cross",
    # KDJ 中 K 与 D 的金叉/死叉。
    "kdj_cross",
    # CCI 越过用户阈值（上下双侧）。
    "cci_threshold",
})

# 一类是阈值穿越：当前值从一侧跨越阈值到另一侧，算"触发"。
ABOVE_BELOW_DIRECTIONS = frozenset({"above", "below"})
# 另一类是零轴/差值线穿越（DIF-DEA / K-D），方向以金叉/死叉命名。
CROSS_DIRECTIONS = frozenset({"bullish_cross", "bearish_cross"})
# 单条告警允许拉取的最大日 K 线天数，避免一次性拉全历史。
MAX_REQUESTED_DAYS = 365


@dataclass
class TechnicalIndicatorAlert:
    """P5 技术指标告警的入库形态。

    Attributes:
        stock_code: 关联股票代码。
        alert_type: 五类指标 enum 中的一种（见 :data:`TECHNICAL_ALERT_TYPES`）。
        indicator_params: 经过 :func:`normalize_indicator_parameters` 标准化后的
            指标参数，例如 MA 的 ``window``、RSI 的 ``period/threshold/direction`` 等。
        metadata: 调用方附加的元信息（例如创建人、备注），不会被本模块使用。
    """

    stock_code: str
    alert_type: str
    indicator_params: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IndicatorEvaluation:
    """单次指标评估的返回值。

    Attributes:
        status: 三态之一：``triggered`` / ``not_triggered`` / ``degraded``。
        observed_value: 当前根的指标观测值（如收盘、RSI、MACD 柱）；不可用时为 ``None``。
        threshold: 对应当前规则的阈值（RSI/CCI）或 0（MACD/KDJ）或 MA 窗口值；
            MA 价穿场景返回的是当前 MA 值，便于前端展示。
        message: 人类可读的提示，会写入告警消息流，文本中必须保留代码原样（禁止改写）。
        data_timestamp: 数据末端对应的日期（已剥离时区），便于审计/去重。
    """

    status: str
    observed_value: Optional[float]
    threshold: Optional[float]
    message: str
    data_timestamp: Optional[datetime] = None


def normalize_indicator_parameters(alert_type: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """标准化并校验一条指标告警的参数。

    对外只暴露这一道闸口：上层（HTTP / 调度）把前端 form 透传进来的"未必可信"
    参数在这里被强制转成合法 dict，确保写库前不会出现：

    * 字符串/缺失字段导致下游 ``int()`` 抛异常；
    * 过大 ``period`` / ``window`` 导致拉数日 K 超过 :data:`MAX_REQUESTED_DAYS`；
    * MACD ``fast >= slow`` 等业务非法组合。

    Args:
        alert_type: 五类指标 enum 中的一种。
        parameters: 原始入参 dict，可能含 ``None`` / 字符串数字 / 缺失字段。

    Returns:
        经过类型与范围校验后的参数 dict，键集与 ``alert_type`` 一一对应。

    Raises:
        ValueError: ``alert_type`` 未知 / 参数类型错 / 范围越界 / 必需 K 线超过上限。
    """
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")

    if alert_type == "ma_price_cross":
        normalized = {
            "direction": _direction(parameters.get("direction"), ABOVE_BELOW_DIRECTIONS, default="above"),
            "window": _int_in_range(parameters.get("window"), "window", default=20),
        }
        return _ensure_required_bars_fetchable(alert_type, normalized)
    if alert_type == "rsi_threshold":
        normalized = {
            "direction": _direction(parameters.get("direction"), ABOVE_BELOW_DIRECTIONS, default="above"),
            "period": _int_in_range(parameters.get("period"), "period", default=12),
            "threshold": _float_in_range(parameters.get("threshold"), "threshold", minimum=0.0, maximum=100.0),
        }
        return _ensure_required_bars_fetchable(alert_type, normalized)
    if alert_type == "macd_cross":
        fast_period = _int_in_range(parameters.get("fast_period"), "fast_period", default=12)
        slow_period = _int_in_range(parameters.get("slow_period"), "slow_period", default=26)
        # 业务约束: 经典 MACD 要求快线周期 < 慢线周期，否则 DIF 永远不反转，
        # 会让金叉/死叉规则失去意义，所以在校验阶段直接拒绝。
        if fast_period >= slow_period:
            raise ValueError("fast_period must be < slow_period")
        normalized = {
            "direction": _direction(parameters.get("direction"), CROSS_DIRECTIONS, default="bullish_cross"),
            "fast_period": fast_period,
            "slow_period": slow_period,
            "signal_period": _int_in_range(parameters.get("signal_period"), "signal_period", default=9),
        }
        return _ensure_required_bars_fetchable(alert_type, normalized)
    if alert_type == "kdj_cross":
        normalized = {
            "direction": _direction(parameters.get("direction"), CROSS_DIRECTIONS, default="bullish_cross"),
            "period": _int_in_range(parameters.get("period"), "period", default=9),
            "k_period": _int_in_range(parameters.get("k_period"), "k_period", default=3),
            "d_period": _int_in_range(parameters.get("d_period"), "d_period", default=3),
        }
        return _ensure_required_bars_fetchable(alert_type, normalized)
    if alert_type == "cci_threshold":
        normalized = {
            "direction": _direction(parameters.get("direction"), ABOVE_BELOW_DIRECTIONS, default="above"),
            "period": _int_in_range(parameters.get("period"), "period", default=14),
            # CCI 通常取值正负 200 附近，但允许任意有限实数，所以使用 _finite_float 而不是 _float_in_range。
            "threshold": _finite_float(parameters.get("threshold"), "threshold"),
        }
        return _ensure_required_bars_fetchable(alert_type, normalized)
    raise ValueError(f"unsupported technical alert_type: {alert_type}")


def compute_required_bars(alert_type: str, params: Dict[str, Any]) -> int:
    """根据指标种类与参数计算"最少需要多少根 K 线"才能算出一个有效指标值。

    所有 EMA/SMA/rolling 序列都需要至少 ``period + 1`` 根才能算出"上一根 +
    当前根"的边沿状态；MACD/KDJ 这种由多层 EMA 构成的指标还要叠加 signal
    平滑期。这些数加 1 是为了确保 ``iloc[-2]`` 也是有效的。
    """
    if alert_type == "ma_price_cross":
        return int(params["window"]) + 1
    if alert_type == "rsi_threshold":
        return int(params["period"]) + 1
    if alert_type == "macd_cross":
        return int(params["slow_period"]) + int(params["signal_period"]) + 1
    if alert_type == "kdj_cross":
        return int(params["period"]) + int(params["k_period"]) + int(params["d_period"]) + 1
    if alert_type == "cci_threshold":
        return int(params["period"]) + 1
    raise ValueError(f"unsupported technical alert_type: {alert_type}")


def compute_requested_days(alert_type: str, params: Dict[str, Any]) -> int:
    """计算"实际去数据源拉多少天日 K"。

    经验公式: ``max(required_bars * 3, required_bars + 30)``，再夹在
    :data:`MAX_REQUESTED_DAYS` 内。预留 30 天的缓冲可以容忍节假日断点，
    3 倍系数可应对长周期指标（例如 window=120）的 warm-up。
    """
    required_bars = compute_required_bars(alert_type, params)
    return min(max(required_bars * 3, required_bars + 30), MAX_REQUESTED_DAYS)


def threshold_for_indicator(alert_type: str, params: Dict[str, Any]) -> Optional[float]:
    """取出当前规则对应的"参考阈值"，便于在 ``degraded`` 分支中仍然展示给用户。

    阈类指标（RSI/CCI）返回用户配置值；零轴交叉类（MACD/KDJ）固定返回 ``0.0``；
    其余（MA 价穿类）返回 ``None`` — 此时 :meth:`_evaluate_ma` 会改用当前 MA 值。
    """
    if alert_type in {"rsi_threshold", "cci_threshold"}:
        return float(params["threshold"])
    if alert_type in {"macd_cross", "kdj_cross"}:
        return 0.0
    return None


def evaluate_indicator_alert(
    alert_type: str,
    stock_code: str,
    params: Dict[str, Any],
    df: Any,
    *,
    now: Optional[datetime] = None,
) -> IndicatorEvaluation:
    """对单只股票 / 单条告警做一次指标评估，返回 :class:`IndicatorEvaluation`。

    Args:
        alert_type: 五类指标 enum 中的一种。
        stock_code: 股票代码，仅用于拼装提示信息，不会触发任何外部数据请求。
        params: 已经过 :func:`normalize_indicator_parameters` 标准化的参数。
        df: 日 K 线 ``DataFrame``；列名大小写、中英文、空格都会被
            :func:`normalize_ohlcv` 归一化。
        now: 用于"去掉当日未收盘 K 线"判断的时间戳；默认 ``datetime.now()``。

    Returns:
        :class:`IndicatorEvaluation`，``status`` 为 ``triggered`` /
        ``not_triggered`` / ``degraded`` 之一。
    """
    columns = ("close",)
    # KDJ / CCI 需要 high/low 用来识别极值，MA/RSI/MACD 仅需收盘价。
    if alert_type in {"kdj_cross", "cci_threshold"}:
        columns = ("high", "low", "close")

    try:
        normalized = normalize_ohlcv(df, required_columns=columns, now=now)
    except ValueError as exc:
        return IndicatorEvaluation(
            status="degraded",
            observed_value=None,
            threshold=threshold_for_indicator(alert_type, params),
            message=str(exc),
        )
    if normalized.empty:
        return IndicatorEvaluation(
            status="degraded",
            observed_value=None,
            threshold=threshold_for_indicator(alert_type, params),
            message="No closed daily data available",
        )
    if len(normalized) < 2:
        return IndicatorEvaluation(
            status="degraded",
            observed_value=None,
            threshold=threshold_for_indicator(alert_type, params),
            message="insufficient closed bars for edge evaluation",
            data_timestamp=_latest_timestamp(normalized),
        )

    required_bars = compute_required_bars(alert_type, params)
    if len(normalized) < required_bars:
        return IndicatorEvaluation(
            status="degraded",
            observed_value=None,
            threshold=threshold_for_indicator(alert_type, params),
            message=f"insufficient data: need {required_bars} bars, got {len(normalized)}",
            data_timestamp=_latest_timestamp(normalized),
        )

    if alert_type == "ma_price_cross":
        return _evaluate_ma(stock_code, params, normalized)
    if alert_type == "rsi_threshold":
        return _evaluate_rsi(stock_code, params, normalized)
    if alert_type == "macd_cross":
        return _evaluate_macd(stock_code, params, normalized)
    if alert_type == "kdj_cross":
        return _evaluate_kdj(stock_code, params, normalized)
    if alert_type == "cci_threshold":
        return _evaluate_cci(stock_code, params, normalized)
    raise ValueError(f"unsupported technical alert_type: {alert_type}")


def normalize_ohlcv(
    df: Any,
    *,
    required_columns: tuple[str, ...],
    now: Optional[datetime] = None,
) -> pd.DataFrame:
    """把上游传入的任意 ``df`` 标准化成 ``date + required_columns`` 的纯净 DataFrame。

    处理流程:

    1. ``df`` 为 ``None`` / 空 / 非 ``DataFrame`` 时直接返回空表；
    2. 用 :func:`_find_column` 兼容"日期/开盘/最高/最低/收盘"中文别名；
    3. 用 ``pd.to_numeric(errors="coerce")`` 强制数值化、缺失列直接 ``ValueError``；
    4. 删除任一必需列为空的行；
    5. 调用 :func:`_drop_partial_today` 裁掉"今日尚未收盘"的最后一行；
    6. 按 ``date`` 稳定排序后重置索引。

    Args:
        df: 原始 ``DataFrame``（可为 ``None`` 或非 DataFrame，本函数容错）。
        required_columns: 必需的列名集合（``date`` 会被自动追加）。
        now: 传给 :func:`_drop_partial_today` 判断"今日"的时间。

    Returns:
        标准化之后的 ``DataFrame``，按 ``date`` 升序、已重置索引，列为 ``date``
        + ``required_columns``。
    """
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame()
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()

    output = pd.DataFrame(index=df.index.copy())
    output["date"] = _date_series(df)

    missing = []
    for canonical in required_columns:
        source = _find_column(df, canonical)
        if source is None:
            missing.append(canonical)
            continue
        output[canonical] = pd.to_numeric(df[source], errors="coerce")
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"daily data missing {missing_text} column")

    output = output.dropna(subset=list(required_columns)).copy()
    if output.empty:
        return output
    # 在 A 股交易时段（A股盘后 16:00 之前）今日 K 线会被标记为 partial，
    # 强行拿来算指标会"用未收盘数据回测当根"，必须裁掉。
    output = _drop_partial_today(output, now=now)
    if output.empty:
        return output.reset_index(drop=True)
    output = output.sort_values(by="date", kind="stable", na_position="first").reset_index(drop=True)
    return output


def _evaluate_ma(stock_code: str, params: Dict[str, Any], df: pd.DataFrame) -> IndicatorEvaluation:
    """收盘价 vs 简单 MA 窗口的边沿触发。

    比较 ``prev_delta = close[-2] - MA[-2]`` 与 ``curr_delta = close[-1] - MA[-1]``，
    若方向反转穿越 0 即认为发生"价穿 MA"。
    """
    window = int(params["window"])
    direction = str(params["direction"])
    series = df["close"].rolling(window=window).mean()
    latest = _latest_timestamp(df)
    prev_close, curr_close = float(df["close"].iloc[-2]), float(df["close"].iloc[-1])
    prev_ma, curr_ma = float(series.iloc[-2]), float(series.iloc[-1])
    if not all(isfinite(value) for value in (prev_ma, curr_ma)):
        return _indicator_unavailable("MA", latest)

    prev_delta = prev_close - prev_ma
    curr_delta = curr_close - curr_ma
    triggered = _crossed_zero(prev_delta, curr_delta, direction)
    message = (
        f"{stock_code} close {curr_close:.4f} crossed {direction} MA{window} {curr_ma:.4f}"
        if triggered
        else f"{stock_code} close {curr_close:.4f} did not edge-cross {direction} MA{window} {curr_ma:.4f}"
    )
    return IndicatorEvaluation(
        status="triggered" if triggered else "not_triggered",
        observed_value=curr_close,
        # MA 价穿类没有固定阈值，把当前 MA 值带回给上层用于展示。
        threshold=curr_ma,
        message=message,
        data_timestamp=latest,
    )


def _evaluate_rsi(stock_code: str, params: Dict[str, Any], df: pd.DataFrame) -> IndicatorEvaluation:
    """Wilder RSI 边沿穿越用户阈值。

    ``triggered`` 的判定: ``prev <= threshold < curr``（above）或对称（below），
    即严格按"上一根在阈值同侧、当前根跨到另一侧"才视为一次有效触发。
    """
    period = int(params["period"])
    threshold = float(params["threshold"])
    direction = str(params["direction"])
    rsi = _calculate_rsi(df["close"], period)
    latest = _latest_timestamp(df)
    prev_value, curr_value = float(rsi.iloc[-2]), float(rsi.iloc[-1])
    if not all(isfinite(value) for value in (prev_value, curr_value)):
        return _indicator_unavailable("RSI", latest, threshold=threshold)

    triggered = _crossed_threshold(prev_value, curr_value, threshold, direction)
    message = (
        f"{stock_code} RSI{period} {curr_value:.2f} crossed {direction} {threshold:.2f}"
        if triggered
        else f"{stock_code} RSI{period} {curr_value:.2f} did not edge-cross {direction} {threshold:.2f}"
    )
    return IndicatorEvaluation(
        status="triggered" if triggered else "not_triggered",
        observed_value=curr_value,
        threshold=threshold,
        message=message,
        data_timestamp=latest,
    )


def _evaluate_macd(stock_code: str, params: Dict[str, Any], df: pd.DataFrame) -> IndicatorEvaluation:
    """MACD 金叉 / 死叉判断。

    计算 ``DIF = EMA(fast) - EMA(slow)``，``DEA = EMA(DIF, signal_period)``，
    监测对象是 ``delta = DIF - DEA``（即 MACD 柱状图）。
    金叉: ``delta`` 由负转正；死叉: 由正转负。
    """
    fast_period = int(params["fast_period"])
    slow_period = int(params["slow_period"])
    signal_period = int(params["signal_period"])
    direction = str(params["direction"])
    ema_fast = df["close"].ewm(span=fast_period, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow_period, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal_period, adjust=False).mean()
    delta = dif - dea
    latest = _latest_timestamp(df)
    prev_delta, curr_delta = float(delta.iloc[-2]), float(delta.iloc[-1])
    if not all(isfinite(value) for value in (prev_delta, curr_delta)):
        return _indicator_unavailable("MACD", latest, threshold=0.0)

    triggered = _crossed_cross_direction(prev_delta, curr_delta, direction)
    message = (
        f"{stock_code} MACD DIF/DEA {direction}: delta = {curr_delta:.4f}"
        if triggered
        else f"{stock_code} MACD delta {curr_delta:.4f} did not edge-cross {direction}"
    )
    return IndicatorEvaluation(
        status="triggered" if triggered else "not_triggered",
        observed_value=curr_delta,
        threshold=0.0,
        message=message,
        data_timestamp=latest,
    )


def _evaluate_kdj(stock_code: str, params: Dict[str, Any], df: pd.DataFrame) -> IndicatorEvaluation:
    """KDJ 金叉 / 死叉判断。

    * 先在 ``period`` 窗口内求 ``highest_high`` 与 ``lowest_low``；
    * ``RSV = (close - lowest) / (highest - lowest) * 100``，分母为 0 时填 50；
    * ``K = EMA(RSV, k_period)``、``D = EMA(K, d_period)``；
    * 监测 ``delta = K - D`` 是否发生"金/死叉"。
    """
    period = int(params["period"])
    k_period = int(params["k_period"])
    d_period = int(params["d_period"])
    direction = str(params["direction"])
    lowest_low = df["low"].rolling(window=period).min()
    highest_high = df["high"].rolling(window=period).max()
    denominator = highest_high - lowest_low
    rsv = ((df["close"] - lowest_low) / denominator.mask(denominator == 0) * 100).fillna(50)
    k_value = rsv.ewm(alpha=1 / k_period, adjust=False).mean()
    d_value = k_value.ewm(alpha=1 / d_period, adjust=False).mean()
    delta = k_value - d_value
    latest = _latest_timestamp(df)
    prev_delta, curr_delta = float(delta.iloc[-2]), float(delta.iloc[-1])
    if not all(isfinite(value) for value in (prev_delta, curr_delta)):
        return _indicator_unavailable("KDJ", latest, threshold=0.0)

    triggered = _crossed_cross_direction(prev_delta, curr_delta, direction)
    message = (
        f"{stock_code} KDJ K/D {direction}: delta = {curr_delta:.4f}"
        if triggered
        else f"{stock_code} KDJ delta {curr_delta:.4f} did not edge-cross {direction}"
    )
    return IndicatorEvaluation(
        status="triggered" if triggered else "not_triggered",
        observed_value=curr_delta,
        threshold=0.0,
        message=message,
        data_timestamp=latest,
    )


def _evaluate_cci(stock_code: str, params: Dict[str, Any], df: pd.DataFrame) -> IndicatorEvaluation:
    """典型 CCI（商品通道指数）边沿越界。

    ``TP = (H + L + C) / 3``；在 ``period`` 窗口内求 ``mean_deviation``；
    ``CCI = (TP - MA(TP)) / (0.015 * mean_deviation)``。
    当 ``mean_deviation == 0`` 时分母会被 mask 成 NaN，上层据此返回 ``degraded``。
    """
    period = int(params["period"])
    threshold = float(params["threshold"])
    direction = str(params["direction"])
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    tp_ma = typical_price.rolling(window=period).mean()
    mean_deviation = typical_price.rolling(window=period).apply(
        lambda values: float(abs(values - values.mean()).mean()),
        raw=False,
    )
    cci = (typical_price - tp_ma) / (0.015 * mean_deviation.mask(mean_deviation == 0))
    latest = _latest_timestamp(df)
    prev_value, curr_value = float(cci.iloc[-2]), float(cci.iloc[-1])
    if not all(isfinite(value) for value in (prev_value, curr_value)):
        return _indicator_unavailable("CCI", latest, threshold=threshold)

    triggered = _crossed_threshold(prev_value, curr_value, threshold, direction)
    message = (
        f"{stock_code} CCI{period} {curr_value:.2f} crossed {direction} {threshold:.2f}"
        if triggered
        else f"{stock_code} CCI{period} {curr_value:.2f} did not edge-cross {direction} {threshold:.2f}"
    )
    return IndicatorEvaluation(
        status="triggered" if triggered else "not_triggered",
        observed_value=curr_value,
        threshold=threshold,
        message=message,
        data_timestamp=latest,
    )


def _ensure_required_bars_fetchable(alert_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """校验"必需 K 线根数"不会超过 :data:`MAX_REQUESTED_DAYS`。

    在 :func:`normalize_indicator_parameters` 末尾调用，提前暴露不可达的配置
    （例如 window=400），避免：上层入库成功但实际拉数据时永远取不到足够样本。
    """
    required_bars = compute_required_bars(alert_type, params)
    if required_bars > MAX_REQUESTED_DAYS:
        raise ValueError(
            f"{alert_type} periods require {required_bars} bars, "
            f"but at most {MAX_REQUESTED_DAYS} days can be requested"
        )
    return params


def _direction(value: Any, allowed: frozenset[str], *, default: str) -> str:
    """把入参方向归一化为 ``allowed`` 中的小写字符串。

    入参允许为空 / 字符串 / 大小写混杂，缺省走 ``default``。
    """
    direction = str(value or default).strip().lower()
    if direction not in allowed:
        raise ValueError(f"invalid direction: {direction}")
    return direction


def _int_in_range(value: Any, field_name: str, *, default: int, minimum: int = 2, maximum: int = 250) -> int:
    """整数解析 + 范围校验的统一入口。

    关键约束: 不仅要 ``int(raw)`` 成功，还要求原始字符串字面量就是整数（含 ``"5"``、
    ``"5.0"``）；这样可以拒掉 ``"5.5"`` / 空串 / 列表等诡异的用户输入。
    """
    raw_value = default if value is None or value == "" else value
    try:
        number = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value}") from exc
    # 反向核对原字面量，确保不是被 int() 截断的小数 / 浮点串。
    if str(raw_value).strip() not in {str(number), f"{number}.0"}:
        raise ValueError(f"{field_name} must be an integer")
    if number < minimum or number > maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return number


def _finite_float(value: Any, field_name: str) -> float:
    """``float()`` 解析并拒绝 ``inf`` / ``nan``。"""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value}") from exc
    if not isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _float_in_range(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """在 :func:`_finite_float` 之上再加上下界校验（用于 RSI 阈值等业务有上下限的场景）。"""
    number = _finite_float(value, field_name)
    if number < minimum or number > maximum:
        raise ValueError(f"{field_name} must be between {minimum:g} and {maximum:g}")
    return number


def _calculate_rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder RSI 计算。

    返回长度为 ``len(close)`` 的 ``Series``，前 ``period`` 个值因 warm-up 不可用
    而为 ``NaN``，下游 :func:`_evaluate_rsi` 已用 ``isfinite`` 守住。
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    # 使用 Wilder's EMA / SMMA 口径，不使用 rolling SMA。
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return (100 - (100 / (1 + rs))).fillna(50)


def _crossed_threshold(prev_value: float, curr_value: float, threshold: float, direction: str) -> bool:
    """判定"上一根在阈值同侧、当前根跨到另一侧"的边沿越界。

    用 ``<=`` 与 ``<``（非 ``<`` 与 ``<=``）来避免"上一根刚好等于阈值"被双重计数。
    """
    if direction == "above":
        return prev_value <= threshold < curr_value
    if direction == "below":
        return prev_value >= threshold > curr_value
    return False


def _crossed_zero(prev_delta: float, curr_delta: float, direction: str) -> bool:
    """MA 价穿用零轴穿越判定，逻辑与 :func:`_crossed_threshold` 一致，但阈值固定为 0。"""
    if direction == "above":
        return prev_delta <= 0 < curr_delta
    if direction == "below":
        return prev_delta >= 0 > curr_delta
    return False


def _crossed_cross_direction(prev_delta: float, curr_delta: float, direction: str) -> bool:
    """MACD / KDJ 金叉死叉判定：监测对象是"差值序列"穿过零轴。"""
    if direction == "bullish_cross":
        return prev_delta <= 0 < curr_delta
    if direction == "bearish_cross":
        return prev_delta >= 0 > curr_delta
    return False


def _indicator_unavailable(
    indicator_name: str,
    data_timestamp: Optional[datetime],
    *,
    threshold: Optional[float] = None,
) -> IndicatorEvaluation:
    """在依赖序列存在但不可计算（如 warm-up 不足、空值）时构造统一的 ``degraded`` 评估。"""
    return IndicatorEvaluation(
        status="degraded",
        observed_value=None,
        threshold=threshold,
        message=f"{indicator_name} value is not available",
        data_timestamp=data_timestamp,
    )


def _find_column(df: pd.DataFrame, canonical: str) -> Optional[Any]:
    """在 ``df.columns`` 中按 canonical 名（小写不区分）查找真实列名。

    支持中英文别名混用（例如 ``"close"`` 或 ``"收盘价"``），返回原始列名供上层
    取值；找不到则返回 ``None``，由 :func:`normalize_ohlcv` 抛 ``ValueError``。
    """
    candidates = {
        "date": ("date", "trade_date", "datetime", "time", "日期", "交易日期"),
        "open": ("open", "open_price", "开盘", "开盘价"),
        "high": ("high", "high_price", "最高", "最高价"),
        "low": ("low", "low_price", "最低", "最低价"),
        "close": ("close", "close_price", "收盘", "收盘价"),
        "volume": ("volume", "vol", "成交量"),
    }
    by_normalized = {str(column).strip().lower(): column for column in df.columns}
    for candidate in candidates[canonical]:
        column = by_normalized.get(candidate.lower())
        if column is not None:
            return column
    return None


def _date_series(df: pd.DataFrame) -> pd.Series:
    """抽出 ``df`` 中的日期列，转为 ``pd.to_datetime`` 后的 ``Series``。

    优先级: 显式 date 列 > ``pd.DatetimeIndex`` > 全 NaT（兜底，调用方再删）。
    """
    date_column = _find_column(df, "date")
    if date_column is not None:
        return pd.to_datetime(df[date_column], errors="coerce")
    index = df.index
    if isinstance(index, pd.DatetimeIndex):
        return pd.Series(index.to_pydatetime(), index=df.index)
    return pd.Series([pd.NaT] * len(df), index=df.index)


def _drop_partial_today(df: pd.DataFrame, *, now: Optional[datetime] = None) -> pd.DataFrame:
    """在 A 股盘后（``16:00`` 之后）返回原 ``df``，否则裁掉最后一行（即当日 partial K 线）。

    A 股收盘时间约 ``15:00``，取 16:00 作为安全边界以容忍行情源推送延迟。
    """
    current = now or datetime.now()
    if current.time() >= time(16, 0):
        return df
    try:
        parsed = pd.to_datetime(df["date"].iloc[-1], errors="coerce")
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return df.iloc[:-1].copy()
    if pd.isna(parsed):
        return df.iloc[:-1].copy()
    last_date = parsed.date()
    if last_date == current.date():
        return df.iloc[:-1].copy()
    return df


def _latest_timestamp(df: pd.DataFrame) -> Optional[datetime]:
    """返回 ``df`` 末行的"日期"对应的 :class:`datetime`（已去时区），用于审计/去重。"""
    try:
        raw_value = df["date"].iloc[-1]
        if pd.isna(raw_value):
            return None
        parsed = pd.to_datetime(raw_value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime().replace(tzinfo=None)
    except Exception:
        return None
