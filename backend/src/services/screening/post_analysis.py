# -*- coding: utf-8 -*-
# Derived from AlphaSift revision 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf.
# Licensed under Apache-2.0 and modified for daily_stock_analysis.
"""可选的 L3 排名后分析器。

DSA 只是分析后端之一；管线也可以使用本地评分卡或外部 HTTP 评分工具，
而不把 DSA 纳入核心选股路径。
"""

from __future__ import annotations

from dataclasses import asdict

import requests

from src.services.screening.config import Config
from src.services.screening.dsa import analyze_picks_with_dsa, apply_dsa_overlay
from src.services.screening.models import Pick
from src.services.screening.normalize import (
    normalize_code,
    safe_float as _safe_float,
    safe_string_list as _safe_string_list,
)


def _normalize_code(value: object) -> str:
    # Pick codes and analyzer response code fields are structured, so
    # US tickers may pass through (see normalize_code docstring).
    """把候选或分析器响应中的代码归一化，允许美股代码原样通过。"""
    return normalize_code(value, allow_ticker=True)

SUPPORTED_POST_ANALYZERS = {"dsa", "scorecard", "external_http"}
_DEFAULT_SCORECARD_PROFILE = {
    "value_quality_value_min": 75.0,
    "value_quality_stability_min": 65.0,
    "value_quality_bonus": 2.4,
    "capital_confirmed_momentum_min": 72.0,
    "capital_confirmed_activity_min": 65.0,
    "capital_confirmed_bonus": 1.8,
    "controlled_reversal_min": 75.0,
    "controlled_reversal_bonus": 1.2,
    "hot_money_activity_min": 90.0,
    "hot_money_stability_max": 45.0,
    "hot_money_penalty": 2.5,
    "volume_spike_ratio": 5.0,
    "volume_spike_penalty": 1.2,
    "high_llm_confidence": 0.75,
    "high_llm_confidence_bonus": 0.8,
    "low_llm_confidence": 0.40,
    "low_llm_confidence_penalty": 1.0,
    "catalyst_bonus": 0.5,
    "catalyst_bonus_cap": 1.5,
    "llm_risk_penalty": 0.8,
    "llm_risk_penalty_cap": 2.4,
    "score_delta_cap": 8.0,
}


def normalize_post_analyzers(analyzers: list[str] | str | None) -> list[str]:
    """把逗号分隔字符串或列表形式的分析器名称归一化为去重后的小写列表。

    同时兼容 ``"dsa,scorecard"`` 与 ``["dsa", "scorecard"]`` 两种写法。

    Args:
        analyzers: 原始输入，None 时返回空列表。

    Returns:
        去重后的分析器名称列表，保持首次出现顺序。

    Raises:
        ValueError: 出现 SUPPORTED_POST_ANALYZERS 之外的名称。
    """
    if analyzers is None:
        return []
    raw_items: list[str]
    if isinstance(analyzers, str):
        raw_items = [analyzers]
    else:
        raw_items = list(analyzers)

    result: list[str] = []
    seen = set()
    for raw in raw_items:
        for item in str(raw).split(","):
            name = item.strip().lower()
            if not name or name in seen:
                continue
            if name not in SUPPORTED_POST_ANALYZERS:
                raise ValueError(
                    f"Unknown post analyzer '{name}'. "
                    f"Supported: {', '.join(sorted(SUPPORTED_POST_ANALYZERS))}"
                )
            seen.add(name)
            result.append(name)
    return result


def run_post_analyzers(
    picks: list[Pick],
    *,
    analyzer_names: list[str],
    run_id: str,
    config: Config,
    max_picks: int | None = None,
    scorecard_profile: dict[str, object] | None = None,
) -> tuple[list[Pick], list[str]]:
    """依次执行选中的后置分析器，并按 final_score 重排。

    ``max_picks`` 是对远程分析器的运行上限。本地 scorecard 是确定且廉价的，
    因此它会跑完全部短名单：让最终分数对每个候选都可比较，从而不影响后续
    "近分轮换"的判定。

    Returns:
        ``(重排后的 picks, 降级消息列表)``。
    """
    if not picks or not analyzer_names:
        return picks, []

    degradation: list[str] = []
    result = picks

    for analyzer in analyzer_names:
        if analyzer == "dsa":
            max_count = max_picks or config.post_analysis_max_picks
            result, messages = _run_dsa_analyzer(result, run_id=run_id, config=config, max_picks=max_count)
        elif analyzer == "scorecard":
            result, messages = _run_scorecard_analyzer(
                result,
                max_picks=len(result),
                profile=scorecard_profile,
            )
        elif analyzer == "external_http":
            max_count = max_picks or config.post_analysis_max_picks
            result, messages = _run_external_http_analyzer(result, run_id=run_id, config=config, max_picks=max_count)
        else:
            messages = [f"Unknown post analyzer skipped: {analyzer}"]
        degradation.extend(messages)
        # 每个分析器都消费上一个的输出，因此这里**逐级**重排而非只在链尾重排：
        # 后面的有上限远程分析器才能看到前一个全池 scorecard 提上来的新候选。
        # Python 的排序是稳定的，同分时保持上一级的相对次序。
        result.sort(key=lambda item: item.final_score, reverse=True)
        for i, pick in enumerate(result, start=1):
            pick.rank = i
    return result, degradation


def _run_dsa_analyzer(
    picks: list[Pick],
    *,
    run_id: str,
    config: Config,
    max_picks: int,
) -> tuple[list[Pick], list[str]]:
    """调用远程 DSA 服务分析候选，并把结果覆盖到 Pick 上。

    Args:
        picks: 候选列表（已排序）。
        run_id: 运行 ID，透传给 DSA。
        config: 选股配置。
        max_picks: 本次最多送检的候选数（运维上限）。

    Returns:
        ``(应用 DSA overlay 后的 picks, 降级消息列表)``。

    Raises:
        ValueError: 未配置 DSA_API_URL。
    """
    if not config.dsa_api_url:
        raise ValueError("post analyzer 'dsa' requested but DSA_API_URL is not configured")

    # 用对象 id 而非 code 记录"真正送检"的候选：overlay 之后列表顺序会变，
    # 只有 id 能稳定区分谁越过了远程上限
    attempted_count = min(max(int(max_picks), 0), len(picks))
    attempted_pick_ids = {id(pick) for pick in picks[:attempted_count]}
    before_scores = {pick.code: float(pick.final_score) for pick in picks}
    analyzed, degradation = analyze_picks_with_dsa(
        picks,
        run_id=run_id,
        api_url=config.dsa_api_url,
        report_type=config.dsa_report_type,
        max_picks=max_picks,
        timeout_sec=config.dsa_timeout_sec,
        force_refresh=config.dsa_force_refresh,
        notify=config.dsa_notify,
    )
    analyzed = apply_dsa_overlay(analyzed)
    for pick in analyzed:
        if id(pick) not in attempted_pick_ids:
            # overlay 之后列表顺序会被重新排序，单看 post-overlay 的索引
            # 区分不出"哪些候选跨过了远程上限"，所以这里直接 skip 未送检的。
            _record_post_result(pick, "dsa", status="skipped", summary="", score_delta=0.0)
            continue

        status = pick.deep_analysis_status
        summary = pick.deep_analysis_summary
        delta = round(float(pick.final_score) - before_scores.get(pick.code, float(pick.final_score)), 4)
        _record_post_result(
            pick,
            "dsa",
            status=status,
            summary=summary,
            score_delta=delta,
            payload=pick.deep_analysis_result or {},
            risk_flags=pick.deep_analysis_risk_flags,
            tags=["dsa"] if status == "completed" else [],
        )

    return analyzed, degradation


def _run_scorecard_analyzer(
    picks: list[Pick],
    *,
    max_picks: int,
    profile: dict[str, object] | None = None,
) -> tuple[list[Pick], list[str]]:
    """对候选用本地评分卡逐一打分并修正 final_score。

    超过 max_picks 的候选被显式标记为 skipped，便于 selection_variant
    在轮换时可靠地排除它们。
    """
    for idx, pick in enumerate(picks):
        if idx >= max_picks:
            # Candidates beyond max_picks are explicitly marked as 'skipped'
            # so selection_variant can reliably exclude them from rotation.
            _record_post_result(pick, "scorecard", status="skipped", summary="", score_delta=0.0)
            continue
        delta, flags, tags, summary = _scorecard_delta(pick, profile=profile)
        pick.final_score = round(float(pick.final_score) + delta, 4)
        pick.risk_flags = _unique([*pick.risk_flags, *flags])
        _record_post_result(
            pick,
            "scorecard",
            status="completed",
            summary=summary,
            score_delta=delta,
            payload={"risk_flags": flags, "tags": tags},
            risk_flags=flags,
            tags=tags,
        )
    return picks, []


def _run_external_http_analyzer(
    picks: list[Pick],
    *,
    run_id: str,
    config: Config,
    max_picks: int,
) -> tuple[list[Pick], list[str]]:
    """调用外部 HTTP 打分服务，按其返回的 score_delta 调整候选分数。

    Args:
        picks: 候选列表。
        run_id: 运行 ID，随请求透传。
        config: 选股配置（需含 post_analyzer_url 与超时）。
        max_picks: 本次最多提交的候选数。

    Returns:
        ``(picks, [])``。

    Raises:
        ValueError: 未配置 POST_ANALYZER_URL，或响应结构非法。
        requests.HTTPError: 远端返回非 2xx。
    """
    if not config.post_analyzer_url:
        raise ValueError("post analyzer 'external_http' requested but POST_ANALYZER_URL is not configured")

    attempted_count = min(max(int(max_picks), 0), len(picks))
    attempted_picks = picks[:attempted_count]
    candidates = [asdict(pick) for pick in attempted_picks]
    response = requests.post(
        config.post_analyzer_url,
        json={"run_id": run_id, "candidates": candidates},
        timeout=config.post_analyzer_timeout_sec,
    )
    response.raise_for_status()
    body = response.json()
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        items = body.get("ranked", [])
    else:
        items = []
    if not isinstance(items, list):
        raise ValueError("external_http analyzer response must be a list or contain ranked=list")

    # 只接受本次请求中提交过的候选的结果：远端不能通过返回额外或过期的 code，
    # 越权修改超出运维上限的那些候选的分数与风险标签
    by_code = {_normalize_code(pick.code): pick for pick in attempted_picks}
    completed_codes: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        code = _normalize_code(item.get("code", ""))
        pick = by_code.get(code)
        if pick is None or code in completed_codes:
            continue
        delta = _safe_float(item.get("score_delta"), 0.0)
        summary = str(item.get("summary", "")).strip()
        risk_flags = _safe_string_list(item.get("risk_flags"))
        tags = _safe_string_list(item.get("tags"))
        pick.final_score = round(float(pick.final_score) + delta, 4)
        pick.risk_flags = _unique([*pick.risk_flags, *risk_flags])
        _record_post_result(
            pick,
            "external_http",
            status="completed",
            summary=summary,
            score_delta=delta,
            payload=item,
            risk_flags=risk_flags,
            tags=tags,
        )

        completed_codes.add(code)

    # A submitted candidate omitted from a syntactically valid response was
    # attempted but not analyzed. Record that explicitly instead of leaving a
    # missing status that downstream consumers cannot distinguish from an
    # analyzer that never ran.
    for pick in attempted_picks:
        if _normalize_code(pick.code) not in completed_codes:
            _record_post_result(
                pick,
                "external_http",
                status="failed",
                summary="",
                score_delta=0.0,
            )

    for pick in picks[attempted_count:]:
        _record_post_result(pick, "external_http", status="skipped", summary="", score_delta=0.0)
    return picks, []


def _scorecard_delta(
    pick: Pick,
    *,
    profile: dict[str, object] | None = None,
) -> tuple[float, list[str], list[str], str]:
    """按评分卡 profile 计算某候选的分数增量并收集风险标记与标签。"""
    profile = _scorecard_profile(profile)
    factors = pick.factor_scores or {}
    value = float(factors.get("value", 50))
    stability = float(factors.get("stability", 50))
    momentum = float(factors.get("momentum", 50))
    activity = float(factors.get("activity", 50))
    reversal = float(factors.get("reversal", 50))

    delta = 0.0
    flags: list[str] = []
    tags: list[str] = []

    if value >= profile["value_quality_value_min"] and stability >= profile["value_quality_stability_min"]:
        delta += profile["value_quality_bonus"]
        tags.append("value_quality")
    if momentum >= profile["capital_confirmed_momentum_min"] and activity >= profile["capital_confirmed_activity_min"]:
        delta += profile["capital_confirmed_bonus"]
        tags.append("capital_confirmed")
    # 反转信号只在当日下跌时算"受控回踩"，上涨时视为趋势延续而不加分
    if reversal >= profile["controlled_reversal_min"] and pick.change_pct < 0:
        delta += profile["controlled_reversal_bonus"]
        tags.append("controlled_reversal")
    if activity >= profile["hot_money_activity_min"] and stability < profile["hot_money_stability_max"]:
        delta -= profile["hot_money_penalty"]
        flags.append("hot_money_instability")
    if pick.volume_ratio is not None and pick.volume_ratio >= profile["volume_spike_ratio"]:
        delta -= profile["volume_spike_penalty"]
        flags.append("volume_spike")
    if pick.llm_confidence is not None:
        if pick.llm_confidence >= profile["high_llm_confidence"]:
            delta += profile["high_llm_confidence_bonus"]
        elif pick.llm_confidence < profile["low_llm_confidence"]:
            delta -= profile["low_llm_confidence_penalty"]
            flags.append("low_llm_confidence")
    if pick.llm_catalysts:
        delta += min(len(pick.llm_catalysts) * profile["catalyst_bonus"], profile["catalyst_bonus_cap"])
    if pick.llm_risks:
        delta -= min(len(pick.llm_risks) * profile["llm_risk_penalty"], profile["llm_risk_penalty_cap"])
        flags.extend(pick.llm_risks)

    # 双向截断：任何单个分析器都不应把分数拉得过大而压过 L2 初排结果
    cap = max(float(profile["score_delta_cap"]), 0.0)
    delta = round(max(min(delta, cap), -cap), 4)
    summary = "本地后置评分: " + (
        "、".join(tags) if tags else "未发现额外加分项"
    )
    if flags:
        summary += f"；风险: {'、'.join(flags[:3])}"
    return delta, _unique(flags), _unique(tags), summary


def _scorecard_profile(profile: dict[str, object] | None) -> dict[str, float]:
    """把传入 profile 与默认阈值合并，返回一份可直接使用的浮点配置表。"""
    result = dict(_DEFAULT_SCORECARD_PROFILE)
    for key, value in (profile or {}).items():
        if key in result:
            result[key] = float(value)
    return result


def _record_post_result(
    pick: Pick,
    analyzer: str,
    *,
    status: str,
    summary: str,
    score_delta: float,
    payload: dict | None = None,
    risk_flags: list[str] | None = None,
    tags: list[str] | None = None,
) -> None:
    """把某个分析器的结果（状态/摘要/分数增量/载荷/标签）写回 Pick。"""
    pick.post_analysis_status[analyzer] = status
    pick.post_analysis_summaries[analyzer] = summary
    pick.post_analysis_score_deltas[analyzer] = round(float(score_delta), 4)
    if payload is not None:
        pick.post_analysis_results[analyzer] = payload
    if risk_flags:
        pick.risk_flags = _unique([*pick.risk_flags, *risk_flags])
    if tags:
        pick.post_analysis_tags = _unique([*pick.post_analysis_tags, *tags])


def _unique(items: list[str]) -> list[str]:
    """按去除首尾空格后的字符串去重，丢弃空串，保持原有顺序。"""
    seen = set()
    result = []
    for item in items:
        key = str(item).strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


