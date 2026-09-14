# -*- coding: utf-8 -*-
"""专家技能意见的冲突感知最终行动综合器。

加权共识仍由 ``SkillAggregator`` 负责。本模块采用保守策略：
检测实质性对立观点，记录审查结果，且只能软化置信度或行动建议。
"""

from __future__ import annotations

from typing import Any, Iterable

from src.agent.protocols import AgentOpinion
from src.agent.skills.defaults import extract_skill_id, is_skill_agent_name

# 信号到数值的映射，用于加权计算
SIGNAL_SCORES = {"strong_buy": 5.0, "buy": 4.0, "hold": 3.0, "sell": 2.0, "strong_sell": 1.0}
# 数值到信号的映射阈值列表（按从高到低排序）
SCORE_TO_SIGNAL = ((4.5, "strong_buy"), (3.5, "buy"), (2.5, "hold"), (1.5, "sell"), (0.0, "strong_sell"))
# 信号到建议行动的映射
SIGNAL_TO_ACTION = {"strong_buy": "buy", "buy": "buy", "hold": "watch", "sell": "reduce", "strong_sell": "sell"}


def synthesize(opinions: Iterable[AgentOpinion], *, weighted_score: float | None = None, confidence: float | None = None) -> dict[str, Any]:
    """为专家技能意见返回可审计的最终信号和行动建议。

    过滤出有效的技能意见，计算加权共识，检测冲突，
    并生成包含最终信号、置信度、共识水平和冲突信息的综合结果。

    Args:
        opinions: 专家技能意见的可迭代对象。
        weighted_score: 预计算的加权分数（可选）。
        confidence: 预计算的置信度（可选）。

    Returns:
        包含最终信号、行动建议、加权分数、置信度、共识水平和冲突信息的字典。
    """
    # 过滤出有效的技能意见（信号在有效范围内且来自技能 Agent）
    views = [opinion for opinion in opinions if is_skill_agent_name(opinion.agent_name) and opinion.signal in SIGNAL_SCORES]
    if not views:
        return _insufficient_result()

    # 如果没有提供预计算值，则计算加权共识
    if weighted_score is None or confidence is None:
        weighted_score, confidence = _confidence_weighted_consensus(views)
    # 将分数和置信度限制在合法范围内
    weighted_score = max(1.0, min(5.0, float(weighted_score)))
    confidence = max(0.0, min(1.0, float(confidence)))
    # 检测意见冲突
    conflicts = _detect_conflicts(views)
    # 根据加权分数确定最终信号
    final_signal = _signal_for_score(weighted_score)
    # 如果存在冲突，降低置信度并可能调整信号
    if conflicts:
        confidence *= 0.85 if conflicts[0]["severity"] == "high" else 0.93
        # 严重的方向性分歧不能产生可操作的交易建议
        if conflicts[0]["severity"] == "high":
            final_signal = "hold"

    return {
        "final_signal": final_signal,
        "final_action": SIGNAL_TO_ACTION[final_signal],
        "weighted_score": round(weighted_score, 4),
        "confidence": round(confidence, 4),
        "consensus_level": _consensus_level(views, conflicts),
        "conflicts": conflicts,
        "deliberation": {
            "status": "completed" if conflicts else "skipped",
            "mode": "deterministic_mediator_v1",
            "responses": _self_review_responses(views, conflicts),
            "self_review": "softened_only",
        },
    }


def _confidence_weighted_consensus(views: list[AgentOpinion]) -> tuple[float, float]:
    """计算置信度加权的共识分数和平均置信度。

    使用每个意见的置信度作为权重，计算加权平均信号分数和加权平均置信度。
    """
    weights = [max(0.01, opinion.confidence) for opinion in views]
    total_weight = sum(weights)
    score = sum(SIGNAL_SCORES[opinion.signal] * weight for opinion, weight in zip(views, weights)) / total_weight
    confidence = sum(opinion.confidence * weight for opinion, weight in zip(views, weights)) / total_weight
    return score, confidence


def _detect_conflicts(views: list[AgentOpinion]) -> list[dict[str, Any]]:
    """检测技能意见中的方向性冲突。

    将意见分为看涨和看跌两组，如果两组都存在且都有高置信度意见，
    则认为存在严重冲突。
    """
    # 分离看涨和看跌意见
    bullish = [opinion for opinion in views if SIGNAL_SCORES[opinion.signal] >= 4.0]
    bearish = [opinion for opinion in views if SIGNAL_SCORES[opinion.signal] <= 2.0]
    if not bullish or not bearish:
        return []
    # 检查是否存在高置信度的方向性分歧
    high_confidence_split = max(opinion.confidence for opinion in bullish) >= 0.7 and max(opinion.confidence for opinion in bearish) >= 0.7
    return [{"type": "directional_opposition", "severity": "high" if high_confidence_split else "medium", "participants": [_skill_name(opinion) for opinion in views]}]


def _self_review_responses(views: list[AgentOpinion], conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """生成自我审查响应，对高置信度分歧进行软化处理。

    当存在冲突时，将强烈信号（strong_buy/strong_sell）软化为较温和的信号，
    并降低置信度。
    """
    if not conflicts:
        return []
    responses = []
    for opinion in views:
        original_signal = opinion.signal
        # 软化强烈信号
        revised_signal = {"strong_buy": "buy", "strong_sell": "sell"}.get(original_signal, original_signal)
        if revised_signal != original_signal or abs(SIGNAL_SCORES[original_signal] - 3.0) >= 1.0:
            responses.append({"skill_id": _skill_name(opinion), "original_signal": original_signal, "revised_signal": revised_signal, "revision": "softened", "revised_confidence": round(opinion.confidence * 0.9, 4)})
    return responses


def _signal_for_score(score: float) -> str:
    """根据加权分数确定对应的信号。

    按阈值从高到低匹配，返回第一个满足条件的信号。
    """
    return next(signal for threshold, signal in SCORE_TO_SIGNAL if score >= threshold)


def _skill_name(opinion: AgentOpinion) -> str:
    """从意见中提取技能名称或返回 Agent 名称。"""
    return extract_skill_id(opinion.agent_name) or opinion.agent_name


def _consensus_level(views: list[AgentOpinion], conflicts: list[dict[str, Any]]) -> str:
    """根据意见数量和冲突情况评估共识水平。

    存在严重冲突时返回 "low"，无冲突且意见数量大于1时返回 "high"，
    否则返回 "medium"。
    """
    if conflicts and conflicts[0]["severity"] == "high":
        return "low"
    return "high" if len(views) > 1 and not conflicts else "medium"


def _insufficient_result() -> dict[str, Any]:
    """当没有有效技能意见时返回的默认结果。

    返回中性的持有信号和零置信度，表示数据不足以做出判断。
    """
    return {"final_signal": "hold", "final_action": "watch", "weighted_score": 3.0, "confidence": 0.0, "consensus_level": "insufficient", "conflicts": [], "deliberation": {"status": "skipped", "responses": [], "self_review": "softened_only"}}
