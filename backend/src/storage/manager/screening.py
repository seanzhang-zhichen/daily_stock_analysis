# -*- coding: utf-8 -*-
"""Persistence operations for stock screening runs."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.storage.models.core import ScreeningRun

logger = logging.getLogger(__name__)


class ScreeningMixin:
    """Read and write screening history with per-user isolation."""

    def save_screening_run(
        self,
        payload: Dict[str, Any],
        *,
        user_id: Optional[int] = None,
    ) -> int:
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return 0
        normalized = dict(payload)
        warnings = self._screening_warning_values(normalized)
        normalized["warnings"] = warnings
        values = {
            "user_id": user_id,
            "strategy": str(normalized.get("strategy") or "").strip() or "unknown",
            "market": str(normalized.get("market") or "").strip() or "cn",
            "snapshot_source": str(normalized.get("snapshot_source") or "").strip() or None,
            "snapshot_count": self._screening_optional_int(normalized.get("snapshot_count")),
            "after_filter_count": self._screening_optional_int(normalized.get("after_filter_count")),
            "candidate_count": self._screening_optional_int(normalized.get("candidate_count")) or 0,
            "llm_ranked": self._screening_optional_bool(normalized.get("llm_ranked")),
            "daily_enriched": self._screening_optional_bool(normalized.get("daily_enriched")),
            "source_errors_json": self._safe_json_dumps(normalized.get("source_errors") or []),
            "warnings_json": self._safe_json_dumps(warnings),
            "result_json": self._safe_json_dumps(normalized),
        }

        try:
            def write(session: Session) -> int:
                row = session.execute(
                    select(ScreeningRun).where(ScreeningRun.run_id == run_id)
                ).scalar_one_or_none()
                if row is None:
                    session.add(ScreeningRun(run_id=run_id, **values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                return 1

            return self._run_write_transaction(f"save_screening_run[{run_id}]", write)
        except Exception as exc:  # Screening itself must remain fail-open.
            logger.warning("Failed to persist screening run %s: %s", run_id, exc)
            return 0

    def list_screening_runs(
        self,
        *,
        limit: int = 20,
        strategy: Optional[str] = None,
        market: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        normalized_limit = max(0, min(int(limit), 100))
        if normalized_limit <= 0:
            return []
        with self.get_session() as session:
            statement = select(ScreeningRun)
            if user_id is not None:
                statement = statement.where(ScreeningRun.user_id == user_id)
            if strategy:
                statement = statement.where(ScreeningRun.strategy == str(strategy).strip())
            if market:
                statement = statement.where(ScreeningRun.market == str(market).strip())
            rows = session.execute(
                statement.order_by(desc(ScreeningRun.created_at), desc(ScreeningRun.id)).limit(normalized_limit)
            ).scalars().all()
            return [self._screening_run_to_dict(row, include_result=False) for row in rows]

    def get_screening_run(
        self,
        run_id: str,
        *,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        with self.get_session() as session:
            statement = select(ScreeningRun).where(ScreeningRun.run_id == normalized_run_id)
            if user_id is not None:
                statement = statement.where(ScreeningRun.user_id == user_id)
            row = session.execute(statement).scalar_one_or_none()
            return self._screening_run_to_dict(row, include_result=True) if row else None

    @staticmethod
    def _screening_optional_int(value: Any) -> Optional[int]:
        try:
            return None if value in (None, "") else int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _screening_optional_bool(value: Any) -> Optional[bool]:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
            return None
        return bool(value)

    @staticmethod
    def _screening_json_list(value: Optional[str]) -> List[Any]:
        try:
            decoded = json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
        return decoded if isinstance(decoded, list) else []

    @staticmethod
    def _screening_text_list(value: Any) -> List[str]:
        values = value if isinstance(value, list) else [value]
        return [str(item).strip() for item in values if str(item or "").strip()]

    @classmethod
    def _screening_warning_values(cls, payload: Dict[str, Any]) -> List[str]:
        warnings: List[str] = []
        for key in ("warnings", "degradation"):
            for item in cls._screening_text_list(payload.get(key)):
                if item not in warnings:
                    warnings.append(item)
        return warnings

    @classmethod
    def _screening_run_to_dict(
        cls,
        row: ScreeningRun,
        *,
        include_result: bool,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "run_id": row.run_id,
            "strategy": row.strategy,
            "market": row.market,
            "snapshot_source": row.snapshot_source or "",
            "snapshot_count": row.snapshot_count,
            "after_filter_count": row.after_filter_count,
            "candidate_count": row.candidate_count,
            "llm_ranked": row.llm_ranked,
            "daily_enriched": row.daily_enriched,
            "source_errors": cls._screening_json_list(row.source_errors_json),
            "warnings": cls._screening_json_list(row.warnings_json),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if include_result:
            try:
                result = json.loads(row.result_json or "{}")
            except (TypeError, ValueError):
                result = {}
            payload["result"] = result if isinstance(result, dict) else {}
        return payload
