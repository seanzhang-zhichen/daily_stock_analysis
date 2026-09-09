"""Persist specialist opinions only after their parent report is durable."""

from __future__ import annotations

from sqlalchemy import select

from src.storage import DatabaseManager, SkillOpinionSampleRecord

_VALID_SIGNALS = {"strong_buy", "buy", "hold", "sell", "strong_sell"}


class SkillOpinionSampleService:
    def __init__(self, db_manager=None):
        self.db = db_manager or DatabaseManager.get_instance()

    def persist(self, *, analysis_history_id: int, stock_code: str, opinions: list[dict]) -> int:
        """Insert one immutable row per selected skill, idempotently."""
        try:
            history_id = int(analysis_history_id)
        except (TypeError, ValueError):
            return 0
        if history_id <= 0 or not str(stock_code or "").strip():
            return 0

        rows = []
        for opinion in opinions:
            if not isinstance(opinion, dict):
                continue
            skill_id = str(opinion.get("skill_id") or "").strip()
            signal = str(opinion.get("signal") or "").strip().lower()
            try:
                confidence = float(opinion.get("confidence"))
            except (TypeError, ValueError):
                continue
            if skill_id and signal in _VALID_SIGNALS and 0.0 <= confidence <= 1.0:
                rows.append((skill_id[:128], signal, confidence))
        if not rows:
            return 0

        def write(session):
            existing = set(session.execute(
                select(SkillOpinionSampleRecord.skill_id).where(
                    SkillOpinionSampleRecord.analysis_history_id == history_id
                )
            ).scalars())
            inserted = 0
            for skill_id, signal, confidence in rows:
                if skill_id not in existing:
                    session.add(SkillOpinionSampleRecord(
                        analysis_history_id=history_id,
                        stock_code=str(stock_code).strip()[:16],
                        skill_id=skill_id,
                        signal=signal,
                        confidence=confidence,
                    ))
                    existing.add(skill_id)
                    inserted += 1
            return inserted

        return self.db._run_write_transaction("persist skill opinion samples", write)
