# -*- coding: utf-8 -*-
"""Decision-signal extraction, lifecycle and query service."""

from __future__ import annotations

import json
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from src.market_context import detect_market
from src.storage import AnalysisHistory, DatabaseManager, DecisionSignalRecord
from src.utils.data_processing import extract_market_structure_context

logger = logging.getLogger(__name__)

ALLOWED_ACTIONS = {"buy", "add", "hold", "reduce", "sell", "watch", "avoid", "alert"}
ALLOWED_STATUSES = {"active", "expired", "invalidated", "closed", "archived"}
TERMINAL_STATUSES = {"expired", "invalidated", "closed", "archived"}
POSITIVE_ACTIONS = {"buy", "add"}
NEGATIVE_ACTIONS = {"reduce", "sell", "avoid"}

ACTION_LABELS = {
    "buy": "买入",
    "add": "加仓",
    "hold": "持有",
    "reduce": "减仓",
    "sell": "卖出",
    "watch": "观望",
    "avoid": "回避",
    "alert": "风险提醒",
}


class DecisionSignalNotFoundError(LookupError):
    """Raised when a user-owned decision signal does not exist."""


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _dump_json(value: Any) -> Optional[str]:
    if value in (None, "", [], {}):
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _text(value: Any, *, limit: Optional[int] = None) -> Optional[str]:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (list, tuple, set)):
        result = "；".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        result = "；".join(
            f"{key}: {item}" for key, item in value.items() if item not in (None, "", [], {})
        )
    else:
        result = str(value).strip()
    if not result:
        return None
    return result[:limit] if limit else result


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _confidence(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value)
        if parsed > 1:
            parsed /= 100
        return max(0.0, min(parsed, 1.0))
    normalized = str(value or "").strip().lower()
    if normalized in {"高", "high"}:
        return 0.85
    if normalized in {"中", "medium", "moderate"}:
        return 0.65
    if normalized in {"低", "low"}:
        return 0.4
    return None


def _action(operation_advice: Any, decision_type: Any, score: Any) -> str:
    advice = str(operation_advice or "").strip().lower()
    exact = {
        "buy": "buy",
        "add": "add",
        "hold": "hold",
        "reduce": "reduce",
        "sell": "sell",
        "watch": "watch",
        "avoid": "avoid",
        "alert": "alert",
    }
    if advice in exact:
        return exact[advice]
    for needles, action in (
        (("加仓", "增持"), "add"),
        (("减仓", "降低仓位"), "reduce"),
        (("卖出", "清仓", "退出", "止损"), "sell"),
        (("回避", "避免", "不建议参与"), "avoid"),
        (("买入", "建仓"), "buy"),
        (("持有", "继续持有"), "hold"),
        (("观望", "观察", "等待"), "watch"),
    ):
        if any(needle in advice for needle in needles):
            return action

    normalized_type = str(decision_type or "").strip().lower()
    if normalized_type == "buy":
        return "buy"
    if normalized_type == "sell":
        return "sell"
    if normalized_type == "hold":
        return "hold"
    try:
        numeric_score = int(score)
    except (TypeError, ValueError):
        numeric_score = 50
    if numeric_score >= 60:
        return "buy"
    if numeric_score < 40:
        return "reduce"
    return "watch"


def _horizon(payload: Dict[str, Any], dashboard: Dict[str, Any]) -> str:
    explicit = str(payload.get("horizon") or "").strip().lower()
    if explicit in {"intraday", "1d", "3d", "5d", "10d", "swing", "long"}:
        return explicit
    core = _json_object(dashboard.get("core_conclusion"))
    sensitivity = str(core.get("time_sensitivity") or "").lower()
    if any(token in sensitivity for token in ("立即", "今日", "intraday", "today")):
        return "intraday"
    if any(token in sensitivity for token in ("本周", "week")):
        return "5d"
    return "3d"


def _expires_at(created_at: datetime, horizon: str) -> datetime:
    days = {
        "intraday": 1,
        "1d": 2,
        "3d": 5,
        "5d": 8,
        "10d": 15,
        "swing": 30,
        "long": 180,
    }.get(horizon, 5)
    return created_at + timedelta(days=days)


def _extract_record(record: AnalysisHistory) -> Dict[str, Any]:
    payload = _json_object(record.raw_result)
    dashboard = _json_object(payload.get("dashboard"))
    core = _json_object(dashboard.get("core_conclusion"))
    intelligence = _json_object(dashboard.get("intelligence"))
    battle_plan = _json_object(dashboard.get("battle_plan"))
    position_strategy = _json_object(battle_plan.get("position_strategy"))
    position_advice = _json_object(core.get("position_advice"))
    market_structure = payload.get("market_structure_context")
    if not isinstance(market_structure, dict):
        market_structure = extract_market_structure_context(record.context_snapshot)
    market_theme = _json_object(market_structure.get("market_theme_context"))
    stock_position = _json_object(market_structure.get("stock_market_position"))
    primary_theme = _json_object(stock_position.get("primary_theme"))

    score = payload.get("sentiment_score", record.sentiment_score)
    action = _action(
        payload.get("operation_advice", record.operation_advice),
        payload.get("decision_type"),
        score,
    )
    horizon = _horizon(payload, dashboard)
    created_at = record.created_at or datetime.now()
    expires_at = _expires_at(created_at, horizon)
    points = [point for point in (record.ideal_buy, record.secondary_buy) if point and point > 0]

    reason = (
        _text(core.get("one_sentence"))
        or _text(payload.get("buy_reason"))
        or _text(record.analysis_summary)
    )
    risk_summary = _text(intelligence.get("risk_alerts")) or _text(payload.get("risk_warning"))
    catalyst_summary = _text(intelligence.get("positive_catalysts"))
    watch_conditions = _text(battle_plan.get("action_checklist"))
    invalidation = _text(position_strategy.get("risk_control")) or risk_summary

    metadata = {
        "report_type": record.report_type,
        "query_id": record.query_id,
        "report_language": payload.get("report_language"),
        "trend_prediction": record.trend_prediction,
        "position_advice": position_advice or None,
        "market_structure_status": market_structure.get("status") if isinstance(market_structure, dict) else None,
        "market_theme_status": market_theme.get("status"),
        "stock_role": stock_position.get("stock_role"),
        "primary_theme": primary_theme.get("name"),
    }

    return {
        "user_id": record.user_id,
        "stock_code": str(record.code or "").strip().upper(),
        "stock_name": _text(record.name, limit=64),
        "market": detect_market(record.code),
        "source_type": "analysis",
        "source_report_id": record.id,
        "trace_id": _text(record.query_id, limit=64),
        "trigger_source": "analysis_history",
        "action": action,
        "action_label": _text(record.operation_advice, limit=32) or ACTION_LABELS[action],
        "confidence": _confidence(payload.get("confidence") or payload.get("confidence_level")),
        "score": int(score) if isinstance(score, (int, float)) else None,
        "horizon": horizon,
        "entry_low": min(points) if points else None,
        "entry_high": max(points) if len(points) > 1 else None,
        "stop_loss": _number(record.stop_loss),
        "target_price": _number(record.take_profit),
        "invalidation": invalidation,
        "watch_conditions": watch_conditions,
        "reason": reason,
        "risk_summary": risk_summary,
        "catalyst_summary": catalyst_summary,
        "evidence_json": _dump_json({
            "trend_analysis": payload.get("trend_analysis"),
            "technical_analysis": payload.get("technical_analysis"),
            "fundamental_analysis": payload.get("fundamental_analysis"),
            "market_structure": {
                "status": market_structure.get("status"),
                "theme_phase": stock_position.get("theme_phase"),
                "stock_role": stock_position.get("stock_role"),
                "primary_theme": primary_theme.get("name"),
            },
        }),
        "data_quality_json": _dump_json({"data_sources": payload.get("data_sources")}),
        "metadata_json": _dump_json(metadata),
        "plan_quality": "complete" if record.stop_loss and record.take_profit and points else "partial",
        "status": "active" if expires_at > datetime.now() else "expired",
        "expires_at": expires_at,
        "created_at": created_at,
        "updated_at": datetime.now(),
    }


def _serialize(record: DecisionSignalRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "stock_code": record.stock_code,
        "stock_name": record.stock_name,
        "market": record.market,
        "source_type": record.source_type,
        "source_report_id": record.source_report_id,
        "trace_id": record.trace_id,
        "trigger_source": record.trigger_source,
        "action": record.action,
        "action_label": record.action_label,
        "confidence": record.confidence,
        "score": record.score,
        "horizon": record.horizon,
        "entry_low": record.entry_low,
        "entry_high": record.entry_high,
        "stop_loss": record.stop_loss,
        "target_price": record.target_price,
        "invalidation": record.invalidation,
        "watch_conditions": record.watch_conditions,
        "reason": record.reason,
        "risk_summary": record.risk_summary,
        "catalyst_summary": record.catalyst_summary,
        "evidence": _json_value(record.evidence_json),
        "data_quality_summary": _json_value(record.data_quality_json),
        "metadata": _json_value(record.metadata_json),
        "plan_quality": record.plan_quality,
        "status": record.status,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


class DecisionSignalService:
    """Provide user-isolated access to structured AI recommendations."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        *,
        portfolio_repo: Optional[Any] = None,
    ):
        self.db = db or getattr(portfolio_repo, "db", None) or DatabaseManager.get_instance()

    @staticmethod
    def _owner_condition(user_id: Optional[int]):
        if user_id is None:
            return DecisionSignalRecord.user_id.is_(None)
        return DecisionSignalRecord.user_id == user_id

    @staticmethod
    def _history_owner_condition(user_id: Optional[int]):
        if user_id is None:
            return AnalysisHistory.user_id.is_(None)
        return AnalysisHistory.user_id == user_id

    def sync_analysis_history(self, *, user_id: Optional[int], limit: int = 500) -> int:
        """Idempotently backfill recent stock-analysis history into decision signals."""
        with self.db.get_session() as session:
            histories = list(
                session.execute(
                    select(AnalysisHistory)
                    .where(
                        self._history_owner_condition(user_id),
                        or_(AnalysisHistory.report_type.is_(None), AnalysisHistory.report_type != "market_review"),
                        AnalysisHistory.code != "MARKET",
                    )
                    .order_by(AnalysisHistory.created_at.desc(), AnalysisHistory.id.desc())
                    .limit(limit)
                ).scalars().all()
            )
            histories.reverse()
            existing_ids = set(
                session.execute(
                    select(DecisionSignalRecord.source_report_id).where(
                        self._owner_condition(user_id),
                        DecisionSignalRecord.source_type == "analysis",
                    )
                ).scalars().all()
            )

        created = 0
        for history in histories:
            if history.id in existing_ids:
                continue
            payload = _extract_record(history)
            try:
                self._create_from_payload(payload)
                created += 1
            except IntegrityError:
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("回填分析历史 AI 建议失败 record_id=%s: %s", history.id, exc)
        return created

    def _create_from_payload(self, payload: Dict[str, Any]) -> DecisionSignalRecord:
        def _write(session):
            record = DecisionSignalRecord(**payload)
            session.add(record)
            session.flush()
            if record.status == "active":
                self._invalidate_opposing(session, record)
            session.refresh(record)
            return record

        return self.db._run_write_transaction("create_decision_signal", _write)

    @staticmethod
    def _invalidate_opposing(session, current: DecisionSignalRecord) -> None:
        if current.action in POSITIVE_ACTIONS:
            opposing = NEGATIVE_ACTIONS
        elif current.action in NEGATIVE_ACTIONS:
            opposing = POSITIVE_ACTIONS
        else:
            return
        rows = session.execute(
            select(DecisionSignalRecord).where(
                DecisionSignalRecord.id != current.id,
                DecisionSignalRecord.user_id == current.user_id,
                DecisionSignalRecord.stock_code == current.stock_code,
                DecisionSignalRecord.status == "active",
                DecisionSignalRecord.action.in_(opposing),
                DecisionSignalRecord.created_at <= current.created_at,
            )
        ).scalars().all()
        now = datetime.now()
        for row in rows:
            row.status = "invalidated"
            row.updated_at = now

    def _expire_due(self, *, user_id: Optional[int]) -> None:
        def _write(session):
            rows = session.execute(
                select(DecisionSignalRecord).where(
                    self._owner_condition(user_id),
                    DecisionSignalRecord.status == "active",
                    DecisionSignalRecord.expires_at.is_not(None),
                    DecisionSignalRecord.expires_at <= datetime.now(),
                )
            ).scalars().all()
            now = datetime.now()
            for row in rows:
                row.status = "expired"
                row.updated_at = now
            return len(rows)

        self.db._run_write_transaction("expire_decision_signals", _write)

    def list_signals(
        self,
        *,
        user_id: Optional[int],
        market: Optional[str] = None,
        stock_code: Optional[str] = None,
        action: Optional[str] = None,
        status: Optional[str] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        self.sync_analysis_history(user_id=user_id)
        self._expire_due(user_id=user_id)

        conditions = [self._owner_condition(user_id)]
        if market:
            conditions.append(DecisionSignalRecord.market == market.lower())
        if stock_code:
            conditions.append(DecisionSignalRecord.stock_code == stock_code.strip().upper())
        if action:
            if action not in ALLOWED_ACTIONS:
                raise ValueError("不支持的建议动作")
            conditions.append(DecisionSignalRecord.action == action)
        if status:
            if status not in ALLOWED_STATUSES:
                raise ValueError("不支持的建议状态")
            conditions.append(DecisionSignalRecord.status == status)
        if created_from:
            conditions.append(DecisionSignalRecord.created_at >= created_from)
        if created_to:
            conditions.append(DecisionSignalRecord.created_at <= created_to)

        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(DecisionSignalRecord.id)).where(and_(*conditions))
            ).scalar_one()
            records = session.execute(
                select(DecisionSignalRecord)
                .where(and_(*conditions))
                .order_by(DecisionSignalRecord.created_at.desc(), DecisionSignalRecord.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars().all()
            return {
                "items": [_serialize(item) for item in records],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    def get_latest_active(
        self,
        *,
        stock_code: str,
        market: Optional[str] = None,
        limit: int = 1,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Compatibility query used by alert-trigger signal linking."""
        items = self.latest(stock_code, user_id=user_id)
        if market:
            items = [item for item in items if item.get("market") == market.lower()]
        safe_limit = max(1, min(int(limit), 20))
        return {"items": items[:safe_limit], "total": len(items)}

    def create_signal(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create an idempotent alert-originated signal from worker payloads."""
        user_id = payload.get("user_id")
        trace_id = _text(payload.get("trace_id"), limit=64)
        source_type = str(payload.get("source_type") or "alert")[:24]
        if trace_id:
            with self.db.get_session() as session:
                existing = session.execute(
                    select(DecisionSignalRecord).where(
                        self._owner_condition(user_id),
                        DecisionSignalRecord.source_type == source_type,
                        DecisionSignalRecord.trace_id == trace_id,
                    ).limit(1)
                ).scalar_one_or_none()
                if existing is not None:
                    return {"item": _serialize(existing), "created": False}

        now = datetime.now()
        horizon = str(payload.get("horizon") or "3d")[:16]
        source_seed = trace_id or json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        source_report_id = payload.get("source_report_id")
        if source_report_id is None:
            source_report_id = -int(hashlib.sha1(source_seed.encode("utf-8")).hexdigest()[:12], 16)
        action = str(payload.get("action") or "alert")
        if action not in ALLOWED_ACTIONS:
            action = "alert"
        fields = {
            "user_id": user_id,
            "stock_code": str(payload.get("stock_code") or "").strip().upper(),
            "stock_name": _text(payload.get("stock_name"), limit=64),
            "market": str(payload.get("market") or detect_market(payload.get("stock_code"))).lower(),
            "source_type": source_type,
            "source_report_id": int(source_report_id),
            "trace_id": trace_id,
            "trigger_source": str(payload.get("trigger_source") or "alert")[:64],
            "action": action,
            "action_label": _text(payload.get("action_label"), limit=32) or ACTION_LABELS[action],
            "horizon": horizon,
            "watch_conditions": _text(payload.get("watch_conditions")),
            "reason": _text(payload.get("reason")),
            "risk_summary": _text(payload.get("risk_summary")),
            "metadata_json": _dump_json(payload.get("metadata")),
            "plan_quality": "minimal",
            "status": "active",
            "expires_at": _expires_at(now, horizon),
            "created_at": now,
            "updated_at": now,
        }
        record = self._create_from_payload(fields)
        return {"item": _serialize(record), "created": True}

    def get_signal(self, signal_id: int, *, user_id: Optional[int]) -> Dict[str, Any]:
        with self.db.get_session() as session:
            record = session.execute(
                select(DecisionSignalRecord).where(
                    DecisionSignalRecord.id == signal_id,
                    self._owner_condition(user_id),
                )
            ).scalar_one_or_none()
            if record is None:
                raise DecisionSignalNotFoundError("AI 建议不存在")
            return _serialize(record)

    def latest(self, stock_code: str, *, user_id: Optional[int]) -> list[Dict[str, Any]]:
        self.sync_analysis_history(user_id=user_id)
        self._expire_due(user_id=user_id)
        with self.db.get_session() as session:
            records = session.execute(
                select(DecisionSignalRecord)
                .where(
                    self._owner_condition(user_id),
                    DecisionSignalRecord.stock_code == stock_code.strip().upper(),
                    DecisionSignalRecord.status == "active",
                )
                .order_by(DecisionSignalRecord.created_at.desc(), DecisionSignalRecord.id.desc())
                .limit(10)
            ).scalars().all()
            return [_serialize(item) for item in records]

    def update_status(
        self,
        signal_id: int,
        *,
        user_id: Optional[int],
        status: str,
    ) -> Dict[str, Any]:
        if status not in TERMINAL_STATUSES:
            raise ValueError("AI 建议只能关闭、失效、归档或标记过期")

        def _write(session):
            record = session.execute(
                select(DecisionSignalRecord).where(
                    DecisionSignalRecord.id == signal_id,
                    self._owner_condition(user_id),
                )
            ).scalar_one_or_none()
            if record is None:
                raise DecisionSignalNotFoundError("AI 建议不存在")
            record.status = status
            record.updated_at = datetime.now()
            session.flush()
            session.refresh(record)
            return _serialize(record)

        return self.db._run_write_transaction("update_decision_signal_status", _write)


__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_STATUSES",
    "DecisionSignalNotFoundError",
    "DecisionSignalService",
]
