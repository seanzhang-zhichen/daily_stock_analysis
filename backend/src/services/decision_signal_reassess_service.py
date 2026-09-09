"""Recompute a signal from its persisted analysis snapshot with deterministic guardrails."""
from __future__ import annotations
import json
from typing import Any, Optional
from src.storage import AnalysisHistory, DatabaseManager, DecisionSignalRecord
from sqlalchemy import select
from src.services.decision_signal_service import DecisionSignalService, _action, _confidence, _json_object, _number, _horizon, _text

class DecisionSignalReassessService:
    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or DatabaseManager.get_instance()
        self.signals = DecisionSignalService(self.db)

    def reassess(self, *, source_report_id: int, user_id: Optional[int], persist: bool = False) -> dict[str, Any]:
        record = self.db.get_analysis_history_by_id(source_report_id, user_id=user_id)
        if record is None:
            raise LookupError(f"source report not found: {source_report_id}")
        if str(record.report_type or "").lower() == "market_review" or str(record.code or "").upper() == "MARKET":
            raise ValueError("source report is not a stock analysis report")
        try:
            payload = json.loads(record.raw_result or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("source report snapshot is invalid")
        dashboard = _json_object(payload.get("dashboard"))
        score = payload.get("sentiment_score", record.sentiment_score)
        action = _action(payload.get("operation_advice", record.operation_advice), payload.get("decision_type"), score)
        confidence = _confidence(payload.get("confidence") or payload.get("confidence_level"))
        horizon = _horizon(payload, dashboard)
        stop_loss, target = _number(record.stop_loss), _number(record.take_profit)
        violations = []
        if action in {"buy", "add"}:
            if confidence is None or confidence < 0.5: violations.append("confidence_below_actionable_threshold")
            if stop_loss is None and not _text(payload.get("invalidation")): violations.append("missing_invalidation_or_stop_loss")
            if not horizon: violations.append("missing_horizon")
        final_action = "watch" if violations else action
        reason = _text(payload.get("analysis_summary")) or _text(record.analysis_summary)
        preview = {"action": final_action, "score": int(score) if isinstance(score, (int, float)) else None,
                   "confidence": confidence, "horizon": horizon, "stop_loss": stop_loss,
                   "target_price": target, "reason": reason,
                   "risk_summary": _text(payload.get("risk_warning")),
                   "metadata": {"raw_action": action, "guardrail_violations": violations, "reassessed": True}}
        if not persist:
            return {"preview": preview, "item": None, "created": False, "warnings": violations}
        if violations and action in {"buy", "add"} and final_action == "watch":
            # Persisting a downgraded watch is safe and preserves the audit trail.
            pass
        fields = {
            "user_id": record.user_id, "stock_code": record.code, "stock_name": record.name,
            "market": "cn" if str(record.code or "").isdigit() else "us", "source_type": "analysis",
            "source_report_id": record.id, "trace_id": f"reassess:{record.id}",
            "trigger_source": "decision_signal_reassess", "action": final_action,
            "score": preview["score"], "confidence": confidence, "horizon": horizon,
            "stop_loss": stop_loss, "target_price": target, "reason": preview["reason"],
            "risk_summary": preview["risk_summary"], "metadata": preview["metadata"],
        }
        # An analysis report may already have its automatic signal. Reassessment
        # updates that authoritative record rather than creating a duplicate.
        with self.db.get_session() as session:
            row = session.execute(select(DecisionSignalRecord).where(
                DecisionSignalRecord.user_id == user_id,
                DecisionSignalRecord.source_type == "analysis",
                DecisionSignalRecord.source_report_id == record.id,
            )).scalar_one_or_none()
            if row is not None:
                for name in ("action", "score", "confidence", "horizon", "stop_loss", "target_price", "reason", "risk_summary"):
                    setattr(row, name, fields[name])
                row.metadata_json = json.dumps(fields["metadata"], ensure_ascii=False)
                session.commit(); session.refresh(row)
                return {"preview": None, "item": self.signals.get_signal(row.id, user_id=user_id), "created": False, "warnings": violations}
        result = self.signals.create_signal(fields)
        return {"preview": None, "item": result["item"], "created": result.get("created", False), "warnings": violations}
