# -*- coding: utf-8 -*-
# Derived from AlphaSift revision 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf.
# Licensed under Apache-2.0 and modified for daily_stock_analysis.
"""L1 硬过滤：把策略的 hard_filters 应用到行情快照 DataFrame 上。"""

import logging
from dataclasses import replace

import pandas as pd

from src.services.screening.models import HardFilterConfig

logger = logging.getLogger(__name__)
_DAILY_FILTER_DEFAULTS = {
    "change_60d_min": None,
    "change_60d_max": None,
    "require_ma_bullish": False,
    "require_price_above_ma20": False,
    "signal_score_min": None,
    "macd_status_whitelist": None,
    "rsi_status_whitelist": None,
    "breakout_20d_pct_min": None,
    "breakout_20d_pct_max": None,
    "range_20d_pct_max": None,
    "volume_ratio_20d_min": None,
    "volume_ratio_20d_max": None,
    "body_pct_min": None,
    "body_pct_max": None,
    "pullback_to_ma20_pct_min": None,
    "pullback_to_ma20_pct_max": None,
    "consolidation_days_20d_min": None,
    "consolidation_days_20d_max": None,
    "volatility_20d_pct_min": None,
    "volatility_20d_pct_max": None,
    "max_drawdown_20d_pct_min": None,
    "max_drawdown_20d_pct_max": None,
    "atr_20_pct_min": None,
    "atr_20_pct_max": None,
}


class SnapshotFieldMissingError(ValueError):
    """快照缺少必需列、导致某个已配置的硬过滤项无法安全求值时抛出。

    继承 ``ValueError`` 便于调用方统一按参数/数据问题处理；刻意不静默跳过，
    否则用户会误以为策略已生效而实际该条件从未参与筛选。
    """


def apply_hard_filters(df: pd.DataFrame, filters: HardFilterConfig) -> pd.DataFrame:
    """按硬条件筛选快照，返回筛选后的副本（不修改入参）。

    所有条件以 ``mask`` 逐项与（AND）累积；值为 None 的过滤项自动跳过，
    因此同一份配置可同时用于"只做基础过滤"和"叠加日线特征"两种场景。

    Args:
        df: 候选池快照，需包含过滤项对应的中英文列名之一。
        filters: 策略的硬过滤配置。

    Returns:
        满足全部已启用条件的行；入参为空时原样返回空表。

    Raises:
        SnapshotFieldMissingError: 已启用的过滤项缺少对应列。
    """
    result = df.copy()
    if result.empty:
        return result

    mask = pd.Series(True, index=result.index)

    if filters.exclude_st:
        # 掩码已全 False 时不再探测列，避免在空结果上仍因缺列而报错
        name_col = _find_col(result, ["name", "股票名称", "名称"]) if mask.any() else None
        if not name_col:
            raise SnapshotFieldMissingError(
                "Missing required snapshot column for exclude_st filter: name"
            )
        # 正则同时排除 ST/*ST 与"退"（退市整理期）标的
        mask &= ~result[name_col].str.contains(r"ST|退", na=False)

    # 数值型过滤项 —— 每一项都是可选的（None 表示不启用）
    mask = _filter_min(result, mask, ["amount", "成交额"], filters.amount_min)
    mask = _filter_min(result, mask, ["price", "最新价", "现价"], filters.price_min)
    mask = _filter_max(result, mask, ["price", "最新价", "现价"], filters.price_max)
    mask = _filter_min(result, mask, ["total_mv", "总市值"], filters.market_cap_min)
    mask = _filter_max(result, mask, ["total_mv", "总市值"], filters.market_cap_max)
    mask = _filter_min(result, mask, ["pe_ratio", "市盈率"], filters.pe_ttm_min)
    mask = _filter_max(result, mask, ["pe_ratio", "市盈率"], filters.pe_ttm_max)
    mask = _filter_min(result, mask, ["pb_ratio", "市净率"], filters.pb_min)
    mask = _filter_max(result, mask, ["pb_ratio", "市净率"], filters.pb_max)
    mask = _filter_min(result, mask, ["volume_ratio", "量比"], filters.volume_ratio_min)
    mask = _filter_min(result, mask, ["turnover_rate", "换手率"], filters.turnover_rate_min)
    mask = _filter_min(result, mask, ["change_pct", "涨跌幅"], filters.change_pct_min)
    mask = _filter_max(result, mask, ["change_pct", "涨跌幅"], filters.change_pct_max)

    mask = _filter_min(result, mask, ["change_60d"], filters.change_60d_min)
    mask = _filter_max(result, mask, ["change_60d"], filters.change_60d_max)
    mask = _filter_bool_true(result, mask, "ma_bullish", filters.require_ma_bullish)
    mask = _filter_bool_true(result, mask, "price_above_ma20", filters.require_price_above_ma20)
    mask = _filter_min(result, mask, ["signal_score"], filters.signal_score_min)
    mask = _filter_in(result, mask, "macd_status", filters.macd_status_whitelist)
    mask = _filter_in(result, mask, "rsi_status", filters.rsi_status_whitelist)
    mask = _filter_min(result, mask, ["breakout_20d_pct"], filters.breakout_20d_pct_min)
    mask = _filter_max(result, mask, ["breakout_20d_pct"], filters.breakout_20d_pct_max)
    mask = _filter_max(result, mask, ["range_20d_pct"], filters.range_20d_pct_max)
    mask = _filter_min(result, mask, ["volume_ratio_20d"], filters.volume_ratio_20d_min)
    mask = _filter_max(result, mask, ["volume_ratio_20d"], filters.volume_ratio_20d_max)
    mask = _filter_min(result, mask, ["body_pct"], filters.body_pct_min)
    mask = _filter_max(result, mask, ["body_pct"], filters.body_pct_max)
    mask = _filter_min(result, mask, ["pullback_to_ma20_pct"], filters.pullback_to_ma20_pct_min)
    mask = _filter_max(result, mask, ["pullback_to_ma20_pct"], filters.pullback_to_ma20_pct_max)
    mask = _filter_min(result, mask, ["consolidation_days_20d"], filters.consolidation_days_20d_min)
    mask = _filter_max(result, mask, ["consolidation_days_20d"], filters.consolidation_days_20d_max)
    mask = _filter_min(result, mask, ["volatility_20d_pct"], filters.volatility_20d_pct_min)
    mask = _filter_max(result, mask, ["volatility_20d_pct"], filters.volatility_20d_pct_max)
    mask = _filter_min(result, mask, ["max_drawdown_20d_pct"], filters.max_drawdown_20d_pct_min)
    mask = _filter_max(result, mask, ["max_drawdown_20d_pct"], filters.max_drawdown_20d_pct_max)
    mask = _filter_min(result, mask, ["atr_20_pct"], filters.atr_20_pct_min)
    mask = _filter_max(result, mask, ["atr_20_pct"], filters.atr_20_pct_max)

    return result.loc[mask].copy()


def hard_filter_rejection_summary(
    df: pd.DataFrame,
    filters: HardFilterConfig,
    *,
    limit: int = 8,
) -> list[str]:
    """逐级统计硬过滤的淘汰数量，返回紧凑的诊断文本列表。

    与 :func:`apply_hard_filters` 使用完全相同的顺序和条件，因此计数可对齐。

    Args:
        df: 候选池快照。
        filters: 策略的硬过滤配置。
        limit: 最多保留多少条淘汰记录；小于等于 0 时直接返回空列表。

    Returns:
        形如 ``"amount_min removed 12 (300->288)"`` 的字符串列表。

    Raises:
        SnapshotFieldMissingError: ``exclude_st`` 启用但缺少名称列。
    """
    if df.empty or limit <= 0:
        return []

    mask = pd.Series(True, index=df.index)
    diagnostics: list[str] = []

    def record(label: str, next_mask: pd.Series) -> None:
        """记录单级过滤的淘汰数，并把当前掩码推进到下一级。"""
        nonlocal mask
        before = int(mask.sum())
        after = int(next_mask.sum())
        removed = before - after
        # 只记录真正淘汰了候选的级别，且不超过 limit 条
        if removed > 0 and len(diagnostics) < limit:
            diagnostics.append(f"{label} removed {removed} ({before}->{after})")
        mask = next_mask

    if filters.exclude_st:
        name_col = _find_col(df, ["name", "股票名称", "名称"]) if mask.any() else None
        if not name_col:
            raise SnapshotFieldMissingError(
                "Missing required snapshot column for exclude_st filter: name"
            )
        record("exclude_st", mask & ~df[name_col].str.contains(r"ST|退", na=False))

    def record_min(label: str, columns: list[str], value: float | None) -> None:
        """配置了下限时记录一次"最小值"过滤步骤。"""
        if value is not None:
            record(label, _filter_min(df, mask, columns, value))

    def record_max(label: str, columns: list[str], value: float | None) -> None:
        """配置了上限时记录一次"最大值"过滤步骤。"""
        if value is not None:
            record(label, _filter_max(df, mask, columns, value))

    record_min("amount_min", ["amount", "成交额"], filters.amount_min)
    record_min("price_min", ["price", "最新价", "现价"], filters.price_min)
    record_max("price_max", ["price", "最新价", "现价"], filters.price_max)
    record_min("market_cap_min", ["total_mv", "总市值"], filters.market_cap_min)
    record_max("market_cap_max", ["total_mv", "总市值"], filters.market_cap_max)
    record_min("pe_ttm_min", ["pe_ratio", "市盈率"], filters.pe_ttm_min)
    record_max("pe_ttm_max", ["pe_ratio", "市盈率"], filters.pe_ttm_max)
    record_min("pb_min", ["pb_ratio", "市净率"], filters.pb_min)
    record_max("pb_max", ["pb_ratio", "市净率"], filters.pb_max)
    record_min("volume_ratio_min", ["volume_ratio", "量比"], filters.volume_ratio_min)
    record_min("turnover_rate_min", ["turnover_rate", "换手率"], filters.turnover_rate_min)
    record_min("change_pct_min", ["change_pct", "涨跌幅"], filters.change_pct_min)
    record_max("change_pct_max", ["change_pct", "涨跌幅"], filters.change_pct_max)
    record_min("change_60d_min", ["change_60d"], filters.change_60d_min)
    record_max("change_60d_max", ["change_60d"], filters.change_60d_max)

    if filters.require_ma_bullish:
        record("require_ma_bullish", _filter_bool_true(df, mask, "ma_bullish", True))
    if filters.require_price_above_ma20:
        record("require_price_above_ma20", _filter_bool_true(df, mask, "price_above_ma20", True))

    record_min("signal_score_min", ["signal_score"], filters.signal_score_min)
    if filters.macd_status_whitelist:
        record("macd_status_whitelist", _filter_in(df, mask, "macd_status", filters.macd_status_whitelist))
    if filters.rsi_status_whitelist:
        record("rsi_status_whitelist", _filter_in(df, mask, "rsi_status", filters.rsi_status_whitelist))
    record_min("breakout_20d_pct_min", ["breakout_20d_pct"], filters.breakout_20d_pct_min)
    record_max("breakout_20d_pct_max", ["breakout_20d_pct"], filters.breakout_20d_pct_max)
    record_max("range_20d_pct_max", ["range_20d_pct"], filters.range_20d_pct_max)
    record_min("volume_ratio_20d_min", ["volume_ratio_20d"], filters.volume_ratio_20d_min)
    record_max("volume_ratio_20d_max", ["volume_ratio_20d"], filters.volume_ratio_20d_max)
    record_min("body_pct_min", ["body_pct"], filters.body_pct_min)
    record_max("body_pct_max", ["body_pct"], filters.body_pct_max)
    record_min("pullback_to_ma20_pct_min", ["pullback_to_ma20_pct"], filters.pullback_to_ma20_pct_min)
    record_max("pullback_to_ma20_pct_max", ["pullback_to_ma20_pct"], filters.pullback_to_ma20_pct_max)
    record_min("consolidation_days_20d_min", ["consolidation_days_20d"], filters.consolidation_days_20d_min)
    record_max("consolidation_days_20d_max", ["consolidation_days_20d"], filters.consolidation_days_20d_max)
    record_min("volatility_20d_pct_min", ["volatility_20d_pct"], filters.volatility_20d_pct_min)
    record_max("volatility_20d_pct_max", ["volatility_20d_pct"], filters.volatility_20d_pct_max)
    record_min("max_drawdown_20d_pct_min", ["max_drawdown_20d_pct"], filters.max_drawdown_20d_pct_min)
    record_max("max_drawdown_20d_pct_max", ["max_drawdown_20d_pct"], filters.max_drawdown_20d_pct_max)
    record_min("atr_20_pct_min", ["atr_20_pct"], filters.atr_20_pct_min)
    record_max("atr_20_pct_max", ["atr_20_pct"], filters.atr_20_pct_max)

    return diagnostics


def hard_filter_waterfall(
    df: pd.DataFrame,
    filters: HardFilterConfig,
    *,
    sample_limit: int = 3,
) -> list[dict[str, object]]:
    """按顺序统计每级硬过滤的淘汰数量，并附带被剔除行的抽样。

    与 :func:`hard_filter_rejection_summary` 共享相同的条件顺序，但额外输出
    每级被淘汰的若干行样本（便于在诊断面板里查看具体是哪些票被过滤）。
    """
    if df.empty:
        return []

    mask = pd.Series(True, index=df.index)
    steps: list[dict[str, object]] = []

    def record(label: str, next_mask: pd.Series, value_columns: list[str] | None = None) -> None:
        """记录一级过滤的淘汰统计（before/after/removed），必要时附带被淘汰行样本。"""
        nonlocal mask
        before = int(mask.sum())
        after = int(next_mask.sum())
        removed = before - after
        step: dict[str, object] = {
            "filter": label,
            "before": before,
            "after": after,
            "removed": removed,
            "removed_pct": round((removed / before * 100.0), 4) if before else 0.0,
        }
        if removed > 0 and sample_limit > 0:
            step["samples"] = _rejection_samples(
                df.loc[mask & ~next_mask],
                value_columns=value_columns or [],
                limit=sample_limit,
            )
        suggestion = _waterfall_suggestion(label, before, after, removed)
        if suggestion:
            step["suggestion"] = suggestion
        steps.append(step)
        mask = next_mask

    if filters.exclude_st:
        name_col = _find_col(df, ["name", "股票名称", "名称"]) if mask.any() else None
        if not name_col:
            raise SnapshotFieldMissingError(
                "Missing required snapshot column for exclude_st filter: name"
            )
        record("exclude_st", mask & ~df[name_col].str.contains(r"ST|退", na=False), [name_col])

    def record_min(label: str, columns: list[str], value: float | None) -> None:
        """配置了下限时记录一次"最小值"过滤步骤（附带相关取值列）。"""
        if value is not None:
            record(label, _filter_min(df, mask, columns, value), columns)

    def record_max(label: str, columns: list[str], value: float | None) -> None:
        """配置了上限时记录一次"最大值"过滤步骤（附带相关取值列）。"""
        if value is not None:
            record(label, _filter_max(df, mask, columns, value), columns)

    record_min("amount_min", ["amount", "成交额"], filters.amount_min)
    record_min("price_min", ["price", "最新价", "现价"], filters.price_min)
    record_max("price_max", ["price", "最新价", "现价"], filters.price_max)
    record_min("market_cap_min", ["total_mv", "总市值"], filters.market_cap_min)
    record_max("market_cap_max", ["total_mv", "总市值"], filters.market_cap_max)
    record_min("pe_ttm_min", ["pe_ratio", "市盈率"], filters.pe_ttm_min)
    record_max("pe_ttm_max", ["pe_ratio", "市盈率"], filters.pe_ttm_max)
    record_min("pb_min", ["pb_ratio", "市净率"], filters.pb_min)
    record_max("pb_max", ["pb_ratio", "市净率"], filters.pb_max)
    record_min("volume_ratio_min", ["volume_ratio", "量比"], filters.volume_ratio_min)
    record_min("turnover_rate_min", ["turnover_rate", "换手率"], filters.turnover_rate_min)
    record_min("change_pct_min", ["change_pct", "涨跌幅"], filters.change_pct_min)
    record_max("change_pct_max", ["change_pct", "涨跌幅"], filters.change_pct_max)
    record_min("change_60d_min", ["change_60d"], filters.change_60d_min)
    record_max("change_60d_max", ["change_60d"], filters.change_60d_max)

    if filters.require_ma_bullish:
        record("require_ma_bullish", _filter_bool_true(df, mask, "ma_bullish", True), ["ma_bullish"])
    if filters.require_price_above_ma20:
        record("require_price_above_ma20", _filter_bool_true(df, mask, "price_above_ma20", True), ["price_above_ma20"])

    record_min("signal_score_min", ["signal_score"], filters.signal_score_min)
    if filters.macd_status_whitelist:
        record("macd_status_whitelist", _filter_in(df, mask, "macd_status", filters.macd_status_whitelist), ["macd_status"])
    if filters.rsi_status_whitelist:
        record("rsi_status_whitelist", _filter_in(df, mask, "rsi_status", filters.rsi_status_whitelist), ["rsi_status"])
    record_min("breakout_20d_pct_min", ["breakout_20d_pct"], filters.breakout_20d_pct_min)
    record_max("breakout_20d_pct_max", ["breakout_20d_pct"], filters.breakout_20d_pct_max)
    record_max("range_20d_pct_max", ["range_20d_pct"], filters.range_20d_pct_max)
    record_min("volume_ratio_20d_min", ["volume_ratio_20d"], filters.volume_ratio_20d_min)
    record_max("volume_ratio_20d_max", ["volume_ratio_20d"], filters.volume_ratio_20d_max)
    record_min("body_pct_min", ["body_pct"], filters.body_pct_min)
    record_max("body_pct_max", ["body_pct"], filters.body_pct_max)
    record_min("pullback_to_ma20_pct_min", ["pullback_to_ma20_pct"], filters.pullback_to_ma20_pct_min)
    record_max("pullback_to_ma20_pct_max", ["pullback_to_ma20_pct"], filters.pullback_to_ma20_pct_max)
    record_min("consolidation_days_20d_min", ["consolidation_days_20d"], filters.consolidation_days_20d_min)
    record_max("consolidation_days_20d_max", ["consolidation_days_20d"], filters.consolidation_days_20d_max)
    record_min("volatility_20d_pct_min", ["volatility_20d_pct"], filters.volatility_20d_pct_min)
    record_max("volatility_20d_pct_max", ["volatility_20d_pct"], filters.volatility_20d_pct_max)
    record_min("max_drawdown_20d_pct_min", ["max_drawdown_20d_pct"], filters.max_drawdown_20d_pct_min)
    record_max("max_drawdown_20d_pct_max", ["max_drawdown_20d_pct"], filters.max_drawdown_20d_pct_max)
    record_min("atr_20_pct_min", ["atr_20_pct"], filters.atr_20_pct_min)
    record_max("atr_20_pct_max", ["atr_20_pct"], filters.atr_20_pct_max)

    return steps


def requires_daily_features(filters: HardFilterConfig) -> bool:
    """判断该硬过滤配置是否依赖日 K 特征列。

    为 True 时必须先为快照补齐日线特征（或由上游决定降级），否则过滤会报错。
    """
    return any([
        filters.change_60d_min is not None,
        filters.change_60d_max is not None,
        filters.require_ma_bullish,
        filters.require_price_above_ma20,
        filters.signal_score_min is not None,
        bool(filters.macd_status_whitelist),
        bool(filters.rsi_status_whitelist),
        filters.breakout_20d_pct_min is not None,
        filters.breakout_20d_pct_max is not None,
        filters.range_20d_pct_max is not None,
        filters.volume_ratio_20d_min is not None,
        filters.volume_ratio_20d_max is not None,
        filters.body_pct_min is not None,
        filters.body_pct_max is not None,
        filters.pullback_to_ma20_pct_min is not None,
        filters.pullback_to_ma20_pct_max is not None,
        filters.consolidation_days_20d_min is not None,
        filters.consolidation_days_20d_max is not None,
        filters.volatility_20d_pct_min is not None,
        filters.volatility_20d_pct_max is not None,
        filters.max_drawdown_20d_pct_min is not None,
        filters.max_drawdown_20d_pct_max is not None,
        filters.atr_20_pct_min is not None,
        filters.atr_20_pct_max is not None,
    ])


def without_daily_filters(filters: HardFilterConfig) -> HardFilterConfig:
    """返回一份禁用所有日 K 过滤项后的配置副本。"""
    return replace(filters, **_DAILY_FILTER_DEFAULTS)


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """从候选列名中返回第一个存在于 DataFrame 的列；都不存在返回 None。"""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _filter_min(
    df: pd.DataFrame,
    mask: pd.Series,
    col_names: list[str],
    value: float | None,
) -> pd.Series:
    """把"列值 >= value"条件并入掩码；value 为 None 或掩码已空则原样返回。

    Raises:
        SnapshotFieldMissingError: 候选列均不存在。
    """
    if value is None:
        return mask
    # 掩码已全 False 时跳过列探测，避免空结果上无谓抛错
    if not mask.any():
        return mask
    col = _find_col(df, col_names)
    if not col:
        raise SnapshotFieldMissingError(
            f"Missing required snapshot column for min filter {col_names}: "
            f"configured value={value}"
        )
    # notna() 必须显式拼上：NaN 与任何值比较都是 False，但仍要区分"缺失"与"不满足"
    series = pd.to_numeric(df[col], errors="coerce")
    return mask & series.ge(value) & series.notna()


def _filter_max(
    df: pd.DataFrame,
    mask: pd.Series,
    col_names: list[str],
    value: float | None,
) -> pd.Series:
    """把"列值 <= value"条件并入掩码；value 为 None 或掩码已空则原样返回。

    Raises:
        SnapshotFieldMissingError: 候选列均不存在。
    """
    if value is None:
        return mask
    if not mask.any():
        return mask
    col = _find_col(df, col_names)
    if not col:
        raise SnapshotFieldMissingError(
            f"Missing required snapshot column for max filter {col_names}: "
            f"configured value={value}"
        )
    series = pd.to_numeric(df[col], errors="coerce")
    return mask & series.le(value) & series.notna()


def _filter_bool_true(
    df: pd.DataFrame,
    mask: pd.Series,
    col_name: str,
    enabled: bool,
) -> pd.Series:
    """仅当启用时要求列值为 True（布尔日 K 特征过滤）。

    Raises:
        SnapshotFieldMissingError: 对应布尔特征列不存在。
    """
    if not enabled:
        return mask
    if not mask.any():
        return mask
    if col_name not in df.columns:
        raise SnapshotFieldMissingError(
            f"Missing required daily feature column for bool filter: {col_name}"
        )
    return mask & (df[col_name] == True)  # noqa: E712


def _filter_in(
    df: pd.DataFrame,
    mask: pd.Series,
    col_name: str,
    allowed: list[str] | None,
) -> pd.Series:
    """把"列值必须命中白名单"条件并入掩码；白名单为空或掩码已空则原样返回。

    比较前统一转字符串，避免快照中状态值是枚举/数值导致匹配不上。

    Raises:
        SnapshotFieldMissingError: 该日线特征列不存在。
    """
    if not allowed:
        return mask
    if not mask.any():
        return mask
    if col_name not in df.columns:
        raise SnapshotFieldMissingError(
            f"Missing required daily feature column for whitelist filter: {col_name}"
        )
    allowed_set = {str(item) for item in allowed}
    return mask & df[col_name].astype(str).isin(allowed_set)



def _rejection_samples(
    df: pd.DataFrame,
    *,
    value_columns: list[str],
    limit: int,
) -> list[dict[str, object]]:
    """构造被淘汰行的展示样本（按 ``limit`` 截断，取头部若干行）。

    每条样本尽量带上代码、名称与触发该过滤的关键取值（value 字段）。
    """
    samples: list[dict[str, object]] = []
    name_col = _find_col(df, ["name", "股票名称", "名称"])
    code_col = _find_col(df, ["code", "代码", "symbol"])
    value_col = _find_col(df, value_columns)
    for _, row in df.head(limit).iterrows():
        item: dict[str, object] = {}
        if code_col:
            item["code"] = str(row.get(code_col, ""))
        if name_col:
            item["name"] = str(row.get(name_col, ""))
        if value_col:
            value = row.get(value_col)
            item["value"] = None if pd.isna(value) else value
        samples.append(item)
    return samples


def _waterfall_suggestion(label: str, before: int, after: int, removed: int) -> str:
    """针对淘汰幅度异常的过滤级给出调参建议；正常情况返回空串。

    两个经验阈值：候选被清零（after == 0）与单级淘汰超过 90%，
    通常意味着阈值设置过严或对应数据大面积缺失。
    """
    if before <= 0 or removed <= 0:
        return ""
    if after == 0:
        return f"{label} eliminated all remaining candidates; inspect threshold or data availability."
    if removed / before >= 0.9:
        return f"{label} removed over 90% of remaining candidates; consider reviewing this threshold."
    return ""


