# -*- coding: utf-8 -*-
# Derived from AlphaSift revision 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf.
# Licensed under Apache-2.0 and modified for daily_stock_analysis.
"""独立风险叠加层（risk overlay）。

对筛选出的候选标的（shortlisted picks）在最终打分之上叠加风险扣分，
输出带风险标签、风险等级与最终排名（rank）的候选列表，供 API 层与报告生成使用。

主要能力：
- 基于默认风险参数（可用 profile 覆盖）逐项评估单一标的风险
- 组合层面的行业/主题集中度扣分，避免 Top N 全压在同一个拥挤交易
- 支持高风险否决（veto）与降级说明（degradation）输出
"""

from __future__ import annotations

from src.services.screening.models import Pick

# 默认风险参数：各风险项的触发阈值与对应扣分。
# 可通过 profile 覆盖任意键，实现不改动代码的策略调参
_DEFAULT_RISK_PROFILE = {
    "chase_change_pct": 8.0,
    "chase_points": 4.0,
    "breakdown_change_pct": -7.0,
    "breakdown_points": 3.5,
    "abnormal_volume_ratio": 6.0,
    "abnormal_volume_ratio_points": 3.0,
    "high_turnover_rate": 15.0,
    "high_turnover_points": 3.0,
    "invalid_pe_points": 3.0,
    "high_pb": 8.0,
    "high_pb_points": 2.0,
    "weak_signal_score": 45.0,
    "weak_signal_points": 2.5,
    "macd_bearish_points": 2.0,
    "rsi_overbought_points": 1.5,
    "low_llm_confidence": 0.35,
    "low_llm_confidence_points": 1.5,
    "llm_risk_points": 1.2,
    "llm_risk_points_cap": 4.0,
    "deep_risk_points": 1.5,
    "deep_risk_points_cap": 4.5,
    "low_daily_quality_score": 70.0,
    "low_daily_quality_points": 2.0,
    "bad_daily_quality_flag_points": 3.0,
    "stale_daily_cache_points": 2.5,
    "fallback_daily_errors_points": 1.5,
    "fetch_failed_daily_points": 6.0,
}
_DEFAULT_PORTFOLIO_BUCKETS = {
    "金融": ("券商", "银行", "保险", "金融"),
    "地产链": ("地产", "房地产", "建材", "家居", "物业"),
    "新能源": ("新能源", "光伏", "锂电", "电池", "储能"),
    "AI算力": ("AI算力", "算力", "数据中心", "服务器", "光模块"),
    "消费": ("白酒", "食品", "家电", "零售", "消费"),
    "医药": ("医药", "医疗", "创新药"),
    "半导体": ("半导体", "芯片"),
}


def apply_risk_overlay(
    picks: list[Pick],
    *,
    max_penalty: float = 12.0,
    veto_high_risk: bool = False,
    profile: dict[str, object] | None = None,
) -> tuple[list[Pick], list[str]]:
    """为候选附上风险标签，并从 final_score 中扣除有上限的风险罚分。

    逐个候选计算风险扣分，写入 risk_penalty / risk_score / risk_level / risk_flags，
    并按 max_penalty 截断后从 final_score 中扣减；可选地否决（veto）高风险候选，
    被否决者进入 degradation 说明而非结果列表。返回按最终得分降序并重新排序的
    候选列表与降级说明。
    """
    if not picks:
        return picks, []

    max_penalty = max(float(max_penalty), 0.0)
    degradation: list[str] = []
    kept: list[Pick] = []
    risk_profile = _risk_profile(profile)

    for pick in picks:
        points, flags = assess_pick_risk(pick, profile=risk_profile)
        penalty = min(points, max_penalty)
        pick.risk_penalty = round(penalty, 4)
        # max_penalty 为 0 时分母为 0，直接记 0 分，避免除零
        pick.risk_score = round(0.0 if max_penalty == 0 else min(points / max_penalty * 100, 100), 4)
        pick.risk_level = _risk_level(points, max_penalty)
        pick.risk_flags = _unique([*pick.risk_flags, *flags])
        pick.final_score = round(float(pick.final_score) - penalty, 4)
        pick.excluded_by_risk = veto_high_risk and pick.risk_level == "high"
        if pick.excluded_by_risk:
            degradation.append(f"Risk veto excluded {pick.code}: {', '.join(pick.risk_flags)}")
            continue
        kept.append(pick)

    kept.sort(key=lambda item: item.final_score, reverse=True)
    for i, pick in enumerate(kept, start=1):
        pick.rank = i
    return kept, degradation


def apply_portfolio_overlay(
    picks: list[Pick],
    *,
    max_same_sector: int = 1,
    concentration_penalty: float = 4.0,
    profile: dict[str, object] | None = None,
) -> tuple[list[Pick], list[str]]:
    """对重复出现的 LLM 行业/主题施加扣分，避免 Top N 全压在同一个拥挤交易。"""
    if not picks:
        return picks, []

    portfolio_profile = profile or {}
    max_same_sector = max(int(portfolio_profile.get("max_same_bucket", max_same_sector)), 1)
    penalty_step = max(float(portfolio_profile.get("concentration_penalty", concentration_penalty)), 0.0)
    if penalty_step == 0:
        return picks, []

    ordered = sorted(picks, key=lambda item: item.final_score, reverse=True)
    if not any(_canonical_sector(_pick_sector(pick)) for pick in ordered):
        return ordered, []

    bucket_counts: dict[str, int] = {}
    bucket_penalties: dict[str, list[tuple[str, str, float]]] = {}
    for pick in ordered:
        sector = _canonical_sector(_pick_sector(pick))
        if not sector:
            continue
        bucket = _portfolio_bucket(sector, pick.llm_theme, buckets=portfolio_profile.get("buckets"))
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        excess = bucket_counts[bucket] - max_same_sector
        if excess <= 0:
            continue

        # 超出额度的扣分封顶为单次扣分的 3 倍，避免同行业堆得越多罚得越重
        penalty = min(penalty_step * excess, penalty_step * 3)
        flag = f"portfolio_sector_concentration:{bucket}"
        pick.portfolio_penalty = round(float(pick.portfolio_penalty) + penalty, 4)
        pick.portfolio_flags = _unique([*pick.portfolio_flags, flag])
        pick.risk_flags = _unique([*pick.risk_flags, flag])
        pick.final_score = round(float(pick.final_score) - penalty, 4)
        bucket_penalties.setdefault(bucket, []).append((pick.code, sector, penalty))

    ordered.sort(key=lambda item: item.final_score, reverse=True)
    for i, pick in enumerate(ordered, start=1):
        pick.rank = i
    notes = [
        (
            f"Portfolio concentration bucket={bucket}: "
            f"penalized={len(items)}, "
            f"codes={_format_penalty_codes(items)}"
        )
        for bucket, items in bucket_penalties.items()
    ]
    return ordered, notes


def assess_pick_risk(
    pick: Pick,
    *,
    profile: dict[str, float] | None = None,
) -> tuple[float, list[str]]:
    """计算单个候选的风险扣分与可读风险标签。

    Returns:
        ``(points, flags)``；points 为未截断的原始扣分，截断由调用方按
        max_penalty 完成。
    """
    profile = _risk_profile(profile)
    points = 0.0
    flags: list[str] = []

    if pick.change_pct >= profile["chase_change_pct"]:
        points += profile["chase_points"]
        flags.append("single_day_chase_risk")
    elif pick.change_pct <= profile["breakdown_change_pct"]:
        points += profile["breakdown_points"]
        flags.append("single_day_breakdown_risk")

    if pick.volume_ratio is not None and pick.volume_ratio >= profile["abnormal_volume_ratio"]:
        points += profile["abnormal_volume_ratio_points"]
        flags.append("abnormal_volume_ratio")
    if pick.turnover_rate is not None and pick.turnover_rate >= profile["high_turnover_rate"]:
        points += profile["high_turnover_points"]
        flags.append("high_turnover")
    if pick.pe_ratio is not None and pick.pe_ratio <= 0:
        points += profile["invalid_pe_points"]
        flags.append("negative_or_invalid_pe")
    if pick.pb_ratio is not None and pick.pb_ratio >= profile["high_pb"]:
        points += profile["high_pb_points"]
        flags.append("high_pb")
    if pick.signal_score is not None and pick.signal_score < profile["weak_signal_score"]:
        points += profile["weak_signal_points"]
        flags.append("weak_daily_signal")
    if pick.macd_status == "bearish":
        points += profile["macd_bearish_points"]
        flags.append("macd_bearish")
    if pick.rsi_status == "overbought":
        points += profile["rsi_overbought_points"]
        flags.append("rsi_overbought")
    if pick.llm_confidence is not None and pick.llm_confidence < profile["low_llm_confidence"]:
        points += profile["low_llm_confidence_points"]
        flags.append("low_llm_confidence")

    daily_points, daily_flags = _assess_daily_data_risk(pick, profile)
    points += daily_points
    flags.extend(daily_flags)

    llm_risks = [risk for risk in pick.llm_risks if risk]
    if llm_risks:
        points += min(len(llm_risks) * profile["llm_risk_points"], profile["llm_risk_points_cap"])
        flags.extend(llm_risks)

    if pick.deep_analysis_risk_flags:
        points += min(
            len(pick.deep_analysis_risk_flags) * profile["deep_risk_points"],
            profile["deep_risk_points_cap"],
        )
        flags.extend(pick.deep_analysis_risk_flags)

    return points, _unique(flags)


def _assess_daily_data_risk(pick: Pick, profile: dict[str, float]) -> tuple[float, list[str]]:
    """把日线历史数据的质量元信息转换为风险扣分。

    根据 daily_quality_score 与 daily_quality_flags 判定数据可信度：
    取数失败、缓存过期、数据源回退等“软”问题分别扣分，而 OHLC 非法、
    价格非正、成交量异常等“硬”问题意味着 K 线本身不可信，另加较重扣分。
    """
    points = 0.0
    flags: list[str] = []

    if pick.daily_quality_score is not None and pick.daily_quality_score < profile["low_daily_quality_score"]:
        points += profile["low_daily_quality_points"]
        flags.append("low_daily_quality")

    quality_flags = _daily_quality_flag_set(pick.daily_quality_flags)
    if "fetch_failed" in quality_flags:
        points += profile["fetch_failed_daily_points"]
        flags.append("daily_fetch_failed")
    if "stale_cache" in quality_flags:
        points += profile["stale_daily_cache_points"]
        flags.append("daily_stale_cache")
    if "fallback_errors" in quality_flags:
        points += profile["fallback_daily_errors_points"]
        flags.append("daily_source_fallback_errors")

    # 这三类标记意味着 K 线数据本身不可信，比缓存过期等"软"问题严重得多
    severe_quality_flags = {"invalid_ohlc", "non_positive_price", "negative_volume"}
    if quality_flags & severe_quality_flags:
        points += profile["bad_daily_quality_flag_points"]
        flags.append("bad_daily_quality_flags")

    return points, flags


def _daily_quality_flag_set(value: str) -> set[str]:
    """把质量标记字符串解析为标记集合；兼容中英文逗号、竖线与空格分隔。"""
    text = str(value or "").strip()
    # pandas 把缺失值写成 "nan"/"<NA>"，需先识别为空集合
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return set()
    normalized = text
    for separator in (",", "，", "|", " "):
        normalized = normalized.replace(separator, ";")
    return {item.strip() for item in normalized.split(";") if item.strip()}


def _risk_level(points: float, max_penalty: float) -> str:
    """按扣分相对 max_penalty 的比例把风险划分为 low/medium/high 三档。"""
    if max_penalty <= 0:
        return "low"
    if points >= max_penalty * 0.66:
        return "high"
    if points >= max_penalty * 0.33:
        return "medium"
    return "low"


def _canonical_sector(label: str) -> str:
    """把行业/板块标签归一化为标准行业名，未命中别名表时返回原标签前 40 字符。"""
    cleaned = str(label or "").strip()[:40]
    if not cleaned:
        return ""
    aliases = {
        "券商": ("券商", "证券"),
        "银行": ("银行",),
        "保险": ("保险",),
        "地产": ("地产", "房地产"),
        "医药": ("医药", "医疗", "创新药"),
        "白酒": ("白酒", "酿酒"),
        "半导体": ("半导体", "芯片"),
        "AI算力": ("AI算力", "算力", "数据中心"),
        "新能源": ("新能源", "光伏", "锂电", "电池"),
    }
    for canonical, needles in aliases.items():
        if any(needle in cleaned for needle in needles):
            return canonical
    return cleaned


def _pick_sector(pick: Pick) -> str:
    """取候选的行业：优先 LLM 判定的行业，回退到基础行业字段。"""
    return pick.llm_sector or pick.industry


def _risk_profile(profile: dict[str, object] | None) -> dict[str, float]:
    """用外部覆盖项合并默认风险参数，忽略默认表中不存在的键。"""
    result = dict(_DEFAULT_RISK_PROFILE)
    for key, value in (profile or {}).items():
        if key in result:
            result[key] = float(value)
    return result


def _portfolio_bucket(
    sector: str,
    theme: str = "",
    *,
    buckets: object = None,
) -> str:
    """把（行业 + 主题）文本映射到组合集中度分桶，未命中自定义分桶时回退到行业名。"""
    text = f"{sector} {theme or ''}"
    bucket_map = _portfolio_buckets(buckets)
    for bucket, needles in bucket_map.items():
        if any(needle in text for needle in needles):
            return bucket
    return sector


def _portfolio_buckets(custom_buckets: object = None) -> dict[str, tuple[str, ...]]:
    """合并默认分桶与自定义分桶，把字符串/列表形式的自定义项统一为元组。"""
    bucket_map = {key: tuple(value) for key, value in _DEFAULT_PORTFOLIO_BUCKETS.items()}
    if not isinstance(custom_buckets, dict):
        return bucket_map
    for bucket, needles in custom_buckets.items():
        if isinstance(needles, str):
            items = [needles]
        elif isinstance(needles, list):
            items = [str(item) for item in needles]
        else:
            continue
        if items:
            bucket_map[str(bucket)] = tuple(items)
    return bucket_map


def _format_penalty_codes(items: list[tuple[str, str, float]], limit: int = 5) -> str:
    """把被扣分的候选格式化为 ``CODE:行业(-扣分)`` 的紧凑字符串，超出部分折叠。"""
    shown = [
        f"{code}:{sector}(-{penalty:.1f})"
        for code, sector, penalty in items[:limit]
    ]
    if len(items) > limit:
        shown.append(f"+{len(items) - limit} more")
    return ",".join(shown)


def _unique(items: list[str]) -> list[str]:
    """对字符串列表去重并去除空白项，保持首次出现顺序。"""
    seen = set()
    result = []
    for item in items:
        key = str(item).strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


