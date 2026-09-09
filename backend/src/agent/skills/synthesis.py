"""Conflict-aware final action synthesis for specialist skill opinions.

The weighted consensus remains the responsibility of ``SkillAggregator``. This
module is deliberately conservative: it detects material opposing views,
records the review, and can only soften confidence or an action.
"""

from __future__ import annotations

from typing import Any, Iterable

from src.agent.protocols import AgentOpinion
from src.agent.skills.defaults import extract_skill_id, is_skill_agent_name

SIGNAL_SCORES = {"strong_buy": 5.0, "buy": 4.0, "hold": 3.0, "sell": 2.0, "strong_sell": 1.0}
SCORE_TO_SIGNAL = ((4.5, "strong_buy"), (3.5, "buy"), (2.5, "hold"), (1.5, "sell"), (0.0, "strong_sell"))
SIGNAL_TO_ACTION = {"strong_buy": "buy", "buy": "buy", "hold": "watch", "sell": "reduce", "strong_sell": "sell"}


def synthesize(opinions: Iterable[AgentOpinion], *, weighted_score: float | None = None, confidence: float | None = None) -> dict[str, Any]:
    """Return an auditable final signal and action for specialist opinions."""
    views = [opinion for opinion in opinions if is_skill_agent_name(opinion.agent_name) and opinion.signal in SIGNAL_SCORES]
    if not views:
        return _insufficient_result()

    if weighted_score is None or confidence is None:
        weighted_score, confidence = _confidence_weighted_consensus(views)
    weighted_score = max(1.0, min(5.0, float(weighted_score)))
    confidence = max(0.0, min(1.0, float(confidence)))
    conflicts = _detect_conflicts(views)
    final_signal = _signal_for_score(weighted_score)
    if conflicts:
        confidence *= 0.85 if conflicts[0]["severity"] == "high" else 0.93
        # A material directional split cannot produce an actionable trade.
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
    weights = [max(0.01, opinion.confidence) for opinion in views]
    total_weight = sum(weights)
    score = sum(SIGNAL_SCORES[opinion.signal] * weight for opinion, weight in zip(views, weights)) / total_weight
    confidence = sum(opinion.confidence * weight for opinion, weight in zip(views, weights)) / total_weight
    return score, confidence


def _detect_conflicts(views: list[AgentOpinion]) -> list[dict[str, Any]]:
    bullish = [opinion for opinion in views if SIGNAL_SCORES[opinion.signal] >= 4.0]
    bearish = [opinion for opinion in views if SIGNAL_SCORES[opinion.signal] <= 2.0]
    if not bullish or not bearish:
        return []
    high_confidence_split = max(opinion.confidence for opinion in bullish) >= 0.7 and max(opinion.confidence for opinion in bearish) >= 0.7
    return [{"type": "directional_opposition", "severity": "high" if high_confidence_split else "medium", "participants": [_skill_name(opinion) for opinion in views]}]


def _self_review_responses(views: list[AgentOpinion], conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not conflicts:
        return []
    responses = []
    for opinion in views:
        original_signal = opinion.signal
        revised_signal = {"strong_buy": "buy", "strong_sell": "sell"}.get(original_signal, original_signal)
        if revised_signal != original_signal or abs(SIGNAL_SCORES[original_signal] - 3.0) >= 1.0:
            responses.append({"skill_id": _skill_name(opinion), "original_signal": original_signal, "revised_signal": revised_signal, "revision": "softened", "revised_confidence": round(opinion.confidence * 0.9, 4)})
    return responses


def _signal_for_score(score: float) -> str:
    return next(signal for threshold, signal in SCORE_TO_SIGNAL if score >= threshold)


def _skill_name(opinion: AgentOpinion) -> str:
    return extract_skill_id(opinion.agent_name) or opinion.agent_name


def _consensus_level(views: list[AgentOpinion], conflicts: list[dict[str, Any]]) -> str:
    if conflicts and conflicts[0]["severity"] == "high":
        return "low"
    return "high" if len(views) > 1 and not conflicts else "medium"


def _insufficient_result() -> dict[str, Any]:
    return {"final_signal": "hold", "final_action": "watch", "weighted_score": 3.0, "confidence": 0.0, "consensus_level": "insufficient", "conflicts": [], "deliberation": {"status": "skipped", "responses": [], "self_review": "softened_only"}}
