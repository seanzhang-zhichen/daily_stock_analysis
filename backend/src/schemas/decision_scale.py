"""Canonical 80/60/40/20 score bands used by reports and signals."""
from typing import Any, Optional
from src.report_language import localize_operation_advice, normalize_report_language

CANONICAL_DECISION_SCALE_VERSION = "decision-scale-v1"
BANDS = ((80, 100, "buy"), (60, 79, "buy"), (40, 59, "watch"), (20, 39, "reduce"), (0, 19, "sell"))

def normalize_score(value: Any) -> Optional[int]:
    try: value = int(float(value))
    except (TypeError, ValueError): return None
    return value if 0 <= value <= 100 else None

def action_for_score(value: Any) -> Optional[str]:
    score = normalize_score(value)
    if score is None: return None
    for low, high, action in BANDS:
        if low <= score <= high: return action
    return None

def score_band_metadata(value: Any) -> dict[str, Any]:
    score = normalize_score(value)
    if score is None: return {}
    for low, high, action in BANDS:
        if low <= score <= high:
            return {"scale_version": CANONICAL_DECISION_SCALE_VERSION, "score": score,
                    "score_band": f"{low}-{high}", "canonical_action": action}
    return {}


def apply_score_action_scale(result: Any) -> list[dict[str, Any]]:
    """Align a result's default action with its canonical score band."""
    score = normalize_score(getattr(result, "sentiment_score", None))
    action = action_for_score(score)
    if score is None or action is None:
        return []
    language = normalize_report_language(getattr(result, "report_language", "zh"))
    decision_type = str(getattr(result, "decision_type", "") or "").strip().lower()
    before_action = {"buy": "buy", "sell": "sell"}.get(decision_type)
    if before_action is None:
        before_action = str(getattr(result, "decision_action", "") or "watch").strip().lower()
    record = {"type": "score_action_alignment", "score": score, "before_action": before_action, "after_action": action, **score_band_metadata(score)}
    result.decision_action = action
    result.decision_type = "buy" if action == "buy" else "sell" if action == "sell" else "hold"
    if before_action != action:
        labels = {"buy": "买入", "watch": "观望", "reduce": "减仓", "sell": "卖出"}
        result.operation_advice = localize_operation_advice(labels[action], language)
    records = getattr(result, "decision_guardrails", None)
    if not isinstance(records, list):
        records = []
        result.decision_guardrails = records
    records.append(record)
    dashboard = getattr(result, "dashboard", None)
    if not isinstance(dashboard, dict):
        dashboard = {}
        result.dashboard = dashboard
    dashboard["decision_action"] = action
    dashboard["decision_guardrails"] = list(records)
    return [record]
