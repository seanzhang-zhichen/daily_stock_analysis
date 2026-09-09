# -*- coding: utf-8 -*-
# Derived from AlphaSift revision 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf.
# Licensed under Apache-2.0 and modified for daily_stock_analysis.
"""``screen_score`` 多因子打分模块。

依据 :class:`ScreeningConfig` 中的 ``factor_weights`` 与 ``scoring_profile``
为筛选候选池中的每一行计算综合得分，并将每个因子分量展开成独立的列：
``factor_value_score`` / ``factor_liquidity_score`` / ...，最终汇总到 0–100
区间的 ``screen_score``。得分越高代表越值得入选，给上层排行榜 / 排序使用。

派生来源: AlphaSift 仓库 (Apache-2.0)；本项目主要修改点:

* 用 :class:`ScreeningConfig` 取代原来的环境变量直读；
* 因子权重归一化逻辑暴露成 :func:`_normalized_factor_weights`；
* 中文业务字段（``industry`` / ``concept`` / 涨跌幅等）兼容。
"""

import pandas as pd

from src.services.screening.models import ScreeningConfig

# 把"因子名"映射到下游 Pick 输出里"以 ``factor_*_score`` 命名的稳定列名"，
# 这样外部消费方可以按名字直接读取，无需关心权重顺序或额外混淆。
_FACTOR_COLUMNS = {
    "value": "factor_value_score",
    "liquidity": "factor_liquidity_score",
    "momentum": "factor_momentum_score",
    "reversal": "factor_reversal_score",
    "activity": "factor_activity_score",
    "stability": "factor_stability_score",
    "size": "factor_size_score",
    "theme_heat": "factor_theme_heat_score",
    "topic_alignment": "factor_topic_alignment_score",
}
# 评分曲线中所有可调的阈值 / 斜率 / 上下限，集中放这里便于把整个模块导出成 JSON 给运营复核。
# 新增字段必须用 ``xxx_slope`` / ``xxx_cap`` 之类的命名约定，否则 :func:`_scoring_profile` 会
# 把它丢掉（按白名单覆盖）。
_DEFAULT_SCORING_PROFILE = {
    "momentum_base": 60.0,
    "momentum_intraday_slope": 5.0,
    "momentum_chase_start_pct": 5.0,
    "momentum_chase_penalty_slope": 10.0,
    "momentum_downside_start_pct": -2.0,
    "momentum_downside_penalty_slope": 3.0,
    "momentum_60d_base": 55.0,
    "momentum_60d_slope": 0.9,
    "momentum_60d_overheat_pct": 45.0,
    "momentum_60d_overheat_penalty_slope": 0.8,
    "momentum_60d_breakdown_pct": -20.0,
    "momentum_60d_breakdown_penalty_slope": 0.7,
    "macd_bullish_bonus": 6.0,
    "macd_bearish_penalty": 8.0,
    "reversal_ideal_change_pct": -3.0,
    "reversal_distance_penalty_slope": 13.0,
    "reversal_collapse_start_pct": -8.0,
    "reversal_collapse_penalty_slope": 10.0,
    "reversal_chase_start_pct": 1.0,
    "reversal_chase_penalty_slope": 8.0,
    "rsi_oversold_bonus": 10.0,
    "rsi_overbought_penalty": 14.0,
    "activity_ideal_volume_ratio": 2.0,
    "activity_volume_ratio_distance_slope": 15.0,
    "activity_high_volume_ratio": 5.0,
    "activity_high_volume_ratio_penalty_slope": 8.0,
    "activity_ideal_turnover_rate": 4.0,
    "activity_turnover_distance_slope": 8.0,
    "activity_high_turnover_rate": 12.0,
    "activity_high_turnover_penalty_slope": 5.0,
    "stability_base": 78.0,
    "stability_change_abs_penalty_slope": 3.0,
    "stability_hot_change_pct": 7.0,
    "stability_hot_change_penalty_slope": 5.0,
    "stability_high_turnover_rate": 10.0,
    "stability_high_turnover_penalty_slope": 2.0,
    "stability_high_volume_ratio": 5.0,
    "stability_high_volume_ratio_penalty_slope": 4.0,
    "stability_invalid_pe_penalty": 18.0,
    "stability_high_volatility_pct": 45.0,
    "stability_high_volatility_penalty_slope": 0.45,
    "stability_max_drawdown_floor_pct": -12.0,
    "stability_drawdown_penalty_slope": 1.2,
    "stability_high_atr_pct": 6.0,
    "stability_high_atr_penalty_slope": 2.0,
    "stability_low_daily_quality_score": 80.0,
    "stability_low_daily_quality_penalty_slope": 0.35,
    "stability_bad_daily_quality_flag_penalty": 8.0,
    "theme_heat_unknown_score": 50.0,
    "theme_heat_change_slope": 6.0,
    "theme_heat_rank_bonus": 10.0,
    "theme_heat_trend_min_observations": 2.0,
    "theme_heat_trend_slope": 0.8,
    "theme_heat_trend_bonus_cap": 10.0,
    "theme_heat_cooling_penalty_slope": 0.8,
    "theme_heat_cooling_penalty_cap": 12.0,
    "theme_heat_persistence_min_score": 60.0,
    "theme_heat_persistence_slope": 0.08,
    "theme_heat_persistence_bonus_cap": 6.0,
    "theme_heat_cooling_score_penalty_slope": 0.6,
    "theme_heat_cooling_score_penalty_cap": 10.0,
    "theme_heat_overheat_score": 88.0,
    "theme_heat_overheat_penalty_slope": 0.5,
    "topic_alignment_unknown_score": 50.0,
    "topic_alignment_match_bonus": 25.0,
    "topic_alignment_heat_weight": 0.25,
    "topic_alignment_unmatched_penalty": 12.0,
}


def compute_screen_scores(df: pd.DataFrame, config: ScreeningConfig) -> pd.DataFrame:
    """为候选池每一行计算 ``screen_score`` 及九个因子分量列。

    Args:
        df: 候选池 ``DataFrame``，至少包含下游各计算函数所需列（PE/PB/amount/change_pct 等）。
        config: 选股运行配置，含因子权重 ``factor_weights`` 与 ``scoring_profile``。

    Returns:
        拷贝自 ``df`` 的结果表，新增 ``factor_*_score`` 九列与 ``screen_score``；
        ``screen_score`` 已被截断到 ``[0, 100]`` 区间。
    """
    result = df.copy()
    factors = _compute_factor_scores(result, config)
    for name, series in factors.items():
        result[_FACTOR_COLUMNS[name]] = series.round(4)

    weights = _normalized_factor_weights(config)
    result["screen_score"] = 0.0
    # 用"线性加权"组装综合分：所有因子分已经是 0–100 区间，整体不需要再 sigmoid，
    # 否则会损失因子间差异。这里夹到 0–100 是为了规避极端上行/下行带来的"溢出"。
    for factor, weight in weights.items():
        if factor in factors:
            result["screen_score"] += factors[factor] * weight

    result["screen_score"] = result["screen_score"].clip(0, 100)

    return result


def factor_score_columns() -> dict[str, str]:
    """返回 Pick 输出里九列稳定列名的映射（``{"value": "factor_value_score", ...}``）。"""
    return dict(_FACTOR_COLUMNS)


def _normalized_factor_weights(config: ScreeningConfig) -> dict[str, float]:
    """把 ``config.factor_weights`` 归一化为"和为 1，仅含 :data:`_FACTOR_COLUMNS` 已知因子"。

    兼容策略:

    * 若 ``factor_weights`` 为空或全部因子都是未知项，按 ``tech_weight`` 推导一组
      legacy 默认权重（value/liquidity/stability/momentum/activity 各 1 份）。
    * 不允许出现负权重（会被夹到 0）；总和为 0 时降级为 hardcoded 默认集。
    """
    raw_weights = config.factor_weights or {
        "value": (1 - config.tech_weight) * 0.50,
        "liquidity": (1 - config.tech_weight) * 0.25,
        "stability": (1 - config.tech_weight) * 0.25,
        "momentum": config.tech_weight * 0.55,
        "activity": config.tech_weight * 0.45,
    }
    weights = {
        factor: max(float(weight), 0.0)
        for factor, weight in raw_weights.items()
        if factor in _FACTOR_COLUMNS
    }
    total = sum(weights.values())
    if total <= 0:
        return {"value": 0.4, "liquidity": 0.2, "momentum": 0.2, "activity": 0.2}
    return {factor: weight / total for factor, weight in weights.items()}


def _compute_factor_scores(df: pd.DataFrame, config: ScreeningConfig | None = None) -> dict[str, pd.Series]:
    """一次性计算全部九个因子分（``value`` / ``liquidity`` / ...）。"""
    config = config or ScreeningConfig()
    profile = _scoring_profile(config)
    return {
        "value": _compute_value_score(df),
        "liquidity": _compute_liquidity_score(df),
        "momentum": _compute_momentum_score(df, profile),
        "reversal": _compute_reversal_score(df, profile),
        "activity": _compute_activity_score(df, profile),
        "stability": _compute_stability_score(df, profile),
        "size": _compute_size_score(df),
        "theme_heat": _compute_theme_heat_score(df, profile),
        "topic_alignment": _compute_topic_alignment_score(df, profile),
    }


def _scoring_profile(config: ScreeningConfig) -> dict[str, float]:
    """合并 ``_DEFAULT_SCORING_PROFILE`` 与 ``config.scoring_profile`` 覆盖项。

    只接受白名单键（默认集合中已有的），避免外部 JSON 注入未知字段引发 numpy
    ``KeyError``；同名键以后者为准，便于运营在线调参。
    """
    profile = dict(_DEFAULT_SCORING_PROFILE)
    for key, value in (config.scoring_profile or {}).items():
        if key in profile:
            profile[key] = float(value)
    return profile


def _compute_snapshot_score(df: pd.DataFrame) -> pd.Series:
    """基于"快照基本面"估算的综合分（0–100）。

    用作兼容回归: 0.5*value + 0.25*liquidity + 0.25*stability，
    当业务想撇开动量看"防御性得分"时可直接调用。
    """
    factors = _compute_factor_scores(df)
    return (
        factors["value"] * 0.50
        + factors["liquidity"] * 0.25
        + factors["stability"] * 0.25
    ).clip(0, 100)


def _compute_tech_score(df: pd.DataFrame) -> pd.Series:
    """纯技术面综合分（0–100），仅用于"快照数据下不可能拿到日 K"的兜底场景。

    ``0.55 * momentum + 0.45 * activity``。真正的技术面打分需要日 K，本文件
    不承担，调用方需提前用 ``daily.py`` 计算并写入 ``signal_score`` 列。
    """
    factors = _compute_factor_scores(df)
    return (factors["momentum"] * 0.55 + factors["activity"] * 0.45).clip(0, 100)


def _compute_value_score(df: pd.DataFrame) -> pd.Series:
    """价值因子：PE/PB 越低越好（前提是正值且不离谱），返回 0–100。

    通过 :func:`_rank_score` 做横截面百分位排名，避免单一极值把整体拉飞。
    """
    score = pd.Series(50.0, index=df.index)

    if "pe_ratio" in df.columns:
        pe = pd.to_numeric(df["pe_ratio"], errors="coerce")
        # 过滤掉负 PE（亏损股）和 >500 的极端值，避免把"看起来便宜但实际无法解读"的样本拉进排名。
        pe_score = _rank_score(pe.where((pe > 0) & (pe < 500)), lower_is_better=True, na_score=25)
        score = score * 0.35 + pe_score * 0.65

    if "pb_ratio" in df.columns:
        pb = pd.to_numeric(df["pb_ratio"], errors="coerce")
        pb_score = _rank_score(pb.where((pb > 0) & (pb < 50)), lower_is_better=True, na_score=25)
        score = score * 0.55 + pb_score * 0.45

    return score.clip(0, 100)


def _compute_liquidity_score(df: pd.DataFrame) -> pd.Series:
    """流动性因子：以成交额的对数做横截面排名（金额越大越好）。"""
    if "amount" not in df.columns:
        return pd.Series(50.0, index=df.index)

    import numpy as np

    amount = pd.to_numeric(df["amount"], errors="coerce")
    # 取对数后再排名，可以压平"沪市大盘股 vs 小市值妖股"的绝对量级差距。
    log_amount = np.log10(amount.clip(lower=1))
    return _rank_score(log_amount.where(amount > 0), lower_is_better=False, na_score=20)


def _compute_momentum_score(df: pd.DataFrame, profile: dict[str, float]) -> pd.Series:
    """动量因子：综合"日内变化 / 60 日趋势 / 信号打分 / MACD 状态"。

    多档叠加各占不同权重，特性:

    * 中等涨幅(+5% 附近)得分最高；过度追高会触发 "chase penalty"；
    * 60 日趋势过强或太弱都会被夹，避免只追极强势股票（高位回撤风险大）或极弱势股票（趋势性下行）；
    * ``macd_status`` 是日级计算产物，缺失时仅记 0。
    """
    score = pd.Series(50.0, index=df.index)

    if "change_pct" in df.columns:
        change = pd.to_numeric(df["change_pct"], errors="coerce").fillna(0)
        # 偏好正向但不过热的日内变化，过度追高会触发 chase penalty。
        intraday_score = profile["momentum_base"] + change * profile["momentum_intraday_slope"]
        intraday_score = intraday_score - (
            change - profile["momentum_chase_start_pct"]
        ).clip(lower=0) * profile["momentum_chase_penalty_slope"]
        intraday_score = intraday_score - (
            -change + profile["momentum_downside_start_pct"]
        ).clip(lower=0) * profile["momentum_downside_penalty_slope"]
        score = score * 0.35 + intraday_score.clip(5, 100) * 0.65

    if "change_60d" in df.columns:
        change_60d = pd.to_numeric(df["change_60d"], errors="coerce").fillna(0)
        trend_score = profile["momentum_60d_base"] + change_60d * profile["momentum_60d_slope"]
        trend_score = trend_score - (
            change_60d - profile["momentum_60d_overheat_pct"]
        ).clip(lower=0) * profile["momentum_60d_overheat_penalty_slope"]
        trend_score = trend_score - (
            -change_60d + profile["momentum_60d_breakdown_pct"]
        ).clip(lower=0) * profile["momentum_60d_breakdown_penalty_slope"]
        score = score * 0.60 + trend_score.clip(5, 100) * 0.40

    if "signal_score" in df.columns:
        signal = pd.to_numeric(df["signal_score"], errors="coerce").fillna(50)
        score = score * 0.70 + signal.clip(0, 100) * 0.30

    if "macd_status" in df.columns:
        macd = df["macd_status"].astype(str)
        score = score + macd.map({
            "bullish": profile["macd_bullish_bonus"],
            "bearish": -profile["macd_bearish_penalty"],
        }).fillna(0)

    return score.clip(5, 100)


def _compute_reversal_score(df: pd.DataFrame, profile: dict[str, float]) -> pd.Series:
    """反转因子：偏好"温和弱势（不崩塌）"的样本，配合 RSI 状态加分。

    当 ``change_pct`` 偏离理想反转点（默认 -3%）过远就扣分；崩盘或追高都重罚；
    60 日涨跌幅再做一次"过激"过滤，避免把 60 日已经大涨 35%+ 的样本也当反转买。
    """
    if "change_pct" not in df.columns:
        return pd.Series(50.0, index=df.index)

    change = pd.to_numeric(df["change_pct"], errors="coerce").fillna(0)
    # 反转机会的精髓是"温和回调，不崩塌"——离理想点越远分越低。
    score = 100 - (
        change - profile["reversal_ideal_change_pct"]
    ).abs() * profile["reversal_distance_penalty_slope"]
    score = score - (
        -change + profile["reversal_collapse_start_pct"]
    ).clip(lower=0) * profile["reversal_collapse_penalty_slope"]
    score = score - (
        change - profile["reversal_chase_start_pct"]
    ).clip(lower=0) * profile["reversal_chase_penalty_slope"]

    if "rsi_status" in df.columns:
        rsi = df["rsi_status"].astype(str)
        score = score + rsi.map({
            "oversold": profile["rsi_oversold_bonus"],
            "overbought": -profile["rsi_overbought_penalty"],
        }).fillna(0)
    if "change_60d" in df.columns:
        change_60d = pd.to_numeric(df["change_60d"], errors="coerce").fillna(0)
        score = score - (change_60d - 35).clip(lower=0) * 0.5
        score = score - (-change_60d - 35).clip(lower=0) * 0.8
    return score.clip(5, 100)


def _compute_activity_score(df: pd.DataFrame, profile: dict[str, float]) -> pd.Series:
    """活跃因子：以"量比 + 换手率"双指标衡量，越贴近理想值越佳。

    越高越偏离"理想值"会被 penalty 拉回；零换手/零量比的样本会被强制降到 40，
    因为它们通常停牌或流动性极差，强行高分会污染排行榜。
    """
    score = pd.Series(50.0, index=df.index)

    if "volume_ratio" in df.columns:
        volume_ratio = pd.to_numeric(df["volume_ratio"], errors="coerce").fillna(1.0)
        vr_score = 100 - (
            volume_ratio - profile["activity_ideal_volume_ratio"]
        ).abs() * profile["activity_volume_ratio_distance_slope"]
        vr_score = vr_score - (
            volume_ratio - profile["activity_high_volume_ratio"]
        ).clip(lower=0) * profile["activity_high_volume_ratio_penalty_slope"]
        score = score * 0.45 + vr_score.clip(5, 100) * 0.55

    if "turnover_rate" in df.columns:
        turnover = pd.to_numeric(df["turnover_rate"], errors="coerce").fillna(0)
        turnover_score = 100 - (
            turnover - profile["activity_ideal_turnover_rate"]
        ).abs() * profile["activity_turnover_distance_slope"]
        turnover_score = turnover_score - (
            turnover - profile["activity_high_turnover_rate"]
        ).clip(lower=0) * profile["activity_high_turnover_penalty_slope"]
        # 零换手通常是停牌或极冷票，对活跃因子无意义，强制中性偏低的 40 分。
        turnover_score = turnover_score.where(turnover > 0, 40)
        score = score * 0.55 + turnover_score.clip(5, 100) * 0.45

    return score.clip(0, 100)


def _compute_stability_score(df: pd.DataFrame, profile: dict[str, float]) -> pd.Series:
    """稳定性因子：以 ``stability_base`` 为锚，逐项扣分；用于筛掉"波动剧烈 / 估值异常"的股票。

    扣分项包括: 价格变化绝对值 / 极端涨幅 / 换手与量比过高 / PE 异常（<=0）/
    波动率 / 回撤 / ATR / 日线质量分及"严重质量标记"。
    """
    score = pd.Series(profile["stability_base"], index=df.index)

    if "change_pct" in df.columns:
        change = pd.to_numeric(df["change_pct"], errors="coerce").fillna(0)
        # 把 |change| 截到 10% 再乘以斜率，避免极端值破坏打分梯度。
        score -= change.abs().clip(upper=10) * profile["stability_change_abs_penalty_slope"]
        score -= (
            change - profile["stability_hot_change_pct"]
        ).clip(lower=0) * profile["stability_hot_change_penalty_slope"]

    if "turnover_rate" in df.columns:
        turnover = pd.to_numeric(df["turnover_rate"], errors="coerce").fillna(0)
        score -= (
            turnover - profile["stability_high_turnover_rate"]
        ).clip(lower=0) * profile["stability_high_turnover_penalty_slope"]

    if "volume_ratio" in df.columns:
        volume_ratio = pd.to_numeric(df["volume_ratio"], errors="coerce").fillna(1)
        score -= (
            volume_ratio - profile["stability_high_volume_ratio"]
        ).clip(lower=0) * profile["stability_high_volume_ratio_penalty_slope"]

    if "pe_ratio" in df.columns:
        pe = pd.to_numeric(df["pe_ratio"], errors="coerce")
        # PE <= 0（亏损股或异常）需要显著扣分；正常正值不扣。
        score = score.where((pe.isna()) | (pe > 0), score - profile["stability_invalid_pe_penalty"])

    if "signal_score" in df.columns:
        signal = pd.to_numeric(df["signal_score"], errors="coerce").fillna(50)
        # signal 偏离 50 越远，越"非典型平庸"，给一点点倾向性加减。
        score = score + (signal - 50) * 0.12

    if "volatility_20d_pct" in df.columns:
        volatility = pd.to_numeric(df["volatility_20d_pct"], errors="coerce")
        score -= (
            volatility - profile["stability_high_volatility_pct"]
        ).clip(lower=0).fillna(0) * profile["stability_high_volatility_penalty_slope"]

    if "max_drawdown_20d_pct" in df.columns:
        drawdown = pd.to_numeric(df["max_drawdown_20d_pct"], errors="coerce")
        score -= (
            profile["stability_max_drawdown_floor_pct"] - drawdown
        ).clip(lower=0).fillna(0) * profile["stability_drawdown_penalty_slope"]

    if "atr_20_pct" in df.columns:
        atr = pd.to_numeric(df["atr_20_pct"], errors="coerce")
        score -= (
            atr - profile["stability_high_atr_pct"]
        ).clip(lower=0).fillna(0) * profile["stability_high_atr_penalty_slope"]

    if "daily_quality_score" in df.columns:
        quality = pd.to_numeric(df["daily_quality_score"], errors="coerce")
        # 低于阈值视为"日线脏"，分越低扣越多。
        score -= (
            profile["stability_low_daily_quality_score"] - quality
        ).clip(lower=0).fillna(0) * profile["stability_low_daily_quality_penalty_slope"]

    if "daily_quality_flags" in df.columns:
        flags = df["daily_quality_flags"].fillna("").astype(str)
        # 任一种"数据严重异常"标记直接 -8 分，挂多个标记也是同样 -8（不叠加）。
        severe_flags = flags.str.contains("invalid_ohlc|non_positive_price|negative_volume|stale_cache")
        score -= severe_flags.astype(float) * profile["stability_bad_daily_quality_flag_penalty"]

    return score.clip(0, 100)


def _compute_size_score(df: pd.DataFrame) -> pd.Series:
    """市值因子：以总市值对数做横截面排名（市值越大分越高，但用对数压平量级）。"""
    if "total_mv" not in df.columns:
        return pd.Series(50.0, index=df.index)

    import numpy as np

    mv = pd.to_numeric(df["total_mv"], errors="coerce")
    # 取对数排名：万亿市值与百亿市值之间不应该出现数量级差距。
    log_mv = np.log10(mv.clip(lower=1))
    return _rank_score(log_mv.where(mv > 0), lower_is_better=False, na_score=35)


def _compute_theme_heat_score(df: pd.DataFrame, profile: dict[str, float]) -> pd.Series:
    """主题热度因子：综合"行业/概念分位 + 趋势/持续/降温 + 过热惩罚"。

    数据源优先级:  ``board_heat_score``（已聚合） > max(行业, 概念) > 涨跌幅 + 排名 fallback；
    缺失观测样本数 < :data:`theme_heat_trend_min_observations` 时不应用趋势奖励。
    """
    base = pd.Series(profile["theme_heat_unknown_score"], index=df.index)
    if "board_heat_score" in df.columns:
        score = pd.to_numeric(df["board_heat_score"], errors="coerce").fillna(base)
    elif "industry_heat_score" in df.columns or "concept_heat_score" in df.columns:
        industry = _numeric_column(df, "industry_heat_score")
        concept = _numeric_column(df, "concept_heat_score")
        # 行业与概念分位取更大者，反映"标的至少挂在一个活跃方向上"。
        score = pd.concat([industry, concept], axis=1).max(axis=1).fillna(base)
    elif "industry_change_pct" in df.columns:
        change = pd.to_numeric(df["industry_change_pct"], errors="coerce").fillna(0)
        score = base + change * profile["theme_heat_change_slope"]
        if "industry_rank" in df.columns:
            rank = pd.to_numeric(df["industry_rank"], errors="coerce")
            # 行业排名前 10 会有 rank bonus；超过 10 名后无奖励。
            score += (
                (profile["theme_heat_rank_bonus"] - rank.clip(lower=1, upper=10))
                .clip(lower=0)
                .fillna(0)
            )
    else:
        return base.clip(0, 100)

    if "board_heat_trend_score" in df.columns:
        trend = pd.to_numeric(df["board_heat_trend_score"], errors="coerce").fillna(0)
        if "board_heat_observations" in df.columns:
            observations = pd.to_numeric(df["board_heat_observations"], errors="coerce").fillna(0)
        else:
            observations = pd.Series(profile["theme_heat_trend_min_observations"], index=df.index)
        # 观测样本不足时不应用 trend 奖励，避免 1 次采样直接决定加分。
        trend_is_reliable = observations >= profile["theme_heat_trend_min_observations"]
        trend_bonus = (trend.clip(lower=0) * profile["theme_heat_trend_slope"]).clip(
            upper=profile["theme_heat_trend_bonus_cap"]
        )
        cooling_penalty = ((-trend).clip(lower=0) * profile["theme_heat_cooling_penalty_slope"]).clip(
            upper=profile["theme_heat_cooling_penalty_cap"]
        )
        score = score + (trend_bonus - cooling_penalty).where(trend_is_reliable, 0)

    if "board_heat_persistence_score" in df.columns:
        persistence = pd.to_numeric(df["board_heat_persistence_score"], errors="coerce").fillna(0)
        persistence_bonus = (
            (persistence - profile["theme_heat_persistence_min_score"]).clip(lower=0)
            * profile["theme_heat_persistence_slope"]
        ).clip(upper=profile["theme_heat_persistence_bonus_cap"])
        score = score + persistence_bonus

    if "board_heat_cooling_score" in df.columns:
        cooling = pd.to_numeric(df["board_heat_cooling_score"], errors="coerce").fillna(0)
        cooling_penalty = (cooling * profile["theme_heat_cooling_score_penalty_slope"]).clip(
            upper=profile["theme_heat_cooling_score_penalty_cap"]
        )
        score = score - cooling_penalty

    overheat = (score - profile["theme_heat_overheat_score"]).clip(lower=0)
    # 过热必须惩罚：高于 overheat_score 的部分按斜率持续扣分。
    score = score - overheat * profile["theme_heat_overheat_penalty_slope"]
    return score.clip(0, 100)


def _compute_topic_alignment_score(df: pd.DataFrame, profile: dict[str, float]) -> pd.Series:
    """题目对齐因子：判断个股的行业/概念标签是否在当前热点话题集合里。

    命中交集 → 加 match bonus，再按 heat 强度加权；不命中 → 扣分。
    与 :func:`_topic_tokens` 共用一份"分词+归一化"规则，避免主题词不匹配导致误判。
    """
    base = pd.Series(profile["topic_alignment_unknown_score"], index=df.index)
    if not {"industry", "concepts", "board_heat_summary"} & set(df.columns):
        return base

    scores = []
    for _, row in df.iterrows():
        candidate_topics = _topic_tokens(row.get("industry")) | _topic_tokens(row.get("concepts"))
        route_topics = _topic_tokens(row.get("board_heat_summary"))
        if not candidate_topics or not route_topics:
            scores.append(float(profile["topic_alignment_unknown_score"]))
            continue
        overlap = candidate_topics & route_topics
        score = float(profile["topic_alignment_unknown_score"])
        if overlap:
            score += float(profile["topic_alignment_match_bonus"])
            heat = pd.to_numeric(row.get("board_heat_score"), errors="coerce")
            if pd.notna(heat):
                # heat > 50 才会继续加分，避免冷门热点反向"刷高"对齐分。
                score += max(float(heat) - 50.0, 0.0) * float(profile["topic_alignment_heat_weight"])
        else:
            score -= float(profile["topic_alignment_unmatched_penalty"])
        scores.append(score)
    return pd.Series(scores, index=df.index).clip(0, 100)


def _topic_tokens(value: object) -> set[str]:
    """把字符串拆成"主题 token 集合"用于判断重叠。

    * 同时容忍 ``|`` / ``,`` / ``，`` / ``;`` / ``；`` / ``/`` 等多种分隔；
    * 形如 ``"通用机械:6"`` 这样的 ``:`` 后是排名，会被截断丢弃；
    * "rank" / "nan" 等占位词也会被剔除。
    """
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return set()
    normalized = text
    for sep in ["|", ",", "，", ";", "；", "/"]:
        normalized = normalized.replace(sep, " ")
    tokens = set()
    for raw in normalized.split():
        token = raw.split(":", 1)[0].strip()
        if token and token.lower() not in {"rank", "nan", "none", "<na>"}:
            tokens.add(token)
    return tokens


def _numeric_column(df: pd.DataFrame, column: str) -> pd.Series:
    """对 ``df`` 上指定列做数值化转换；缺失列返回全 NA 的 ``Series``。"""
    if column not in df.columns:
        return pd.Series(pd.NA, index=df.index)
    return pd.to_numeric(df[column], errors="coerce")


def _rank_score(
    series: pd.Series,
    *,
    lower_is_better: bool,
    na_score: float = 50.0,
) -> pd.Series:
    """将一列数值映射成 0–100 的横截面百分位分。

    全 NaN 时返回 ``na_score``；非 NaN 时按百分位排名映射回 0–100，便于各因子分
    直接线性相加而不被极端值带偏。
    """
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() == 0:
        return pd.Series(float(na_score), index=series.index)

    ranks = numeric.rank(
        ascending=not lower_is_better,
        na_option="keep",
        pct=True,
    ) * 100
    return ranks.fillna(float(na_score)).clip(0, 100)


