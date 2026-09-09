"""Evaluate pending specialist opinions from locally persisted daily bars."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from src.storage import (
    DatabaseManager,
    SkillOpinionOutcomeRecord,
    SkillOpinionSampleRecord,
    StockDaily,
)
from src.services.skill_opinion_weight_service import ENGINE_VERSION

HORIZONS = {"1d": 1, "3d": 3, "5d": 5, "10d": 10}


class SkillOpinionOutcomeService:
    """Best-effort local evaluator; it never fetches quotes or blocks analysis."""

    def __init__(self, db_manager=None):
        self.db = db_manager or DatabaseManager.get_instance()

    def evaluate_pending(self, *, limit: int = 200) -> dict[str, int]:
        """Evaluate missing pending sample/horizon keys using locally stored bars."""
        limit = max(1, min(int(limit), 500))
        completed = pending = created = 0
        with self.db.get_session() as session:
            samples = session.execute(
                select(SkillOpinionSampleRecord).order_by(SkillOpinionSampleRecord.id).limit(limit)
            ).scalars().all()
            for sample in samples:
                for horizon, days in HORIZONS.items():
                    existing = session.execute(select(SkillOpinionOutcomeRecord).where(
                        SkillOpinionOutcomeRecord.sample_id == sample.id,
                        SkillOpinionOutcomeRecord.horizon == horizon,
                        SkillOpinionOutcomeRecord.engine_version == ENGINE_VERSION,
                    )).scalar_one_or_none()
                    if existing is not None and existing.eval_status != "pending":
                        continue
                    bars = session.execute(select(StockDaily).where(
                        StockDaily.code == sample.stock_code,
                        StockDaily.date >= sample.created_at.date(),
                    ).order_by(StockDaily.date).limit(days + 1)).scalars().all()
                    status, outcome, correct, stock_return = self._evaluate(sample.signal, bars, days)
                    if status == "pending":
                        pending += 1
                    else:
                        completed += 1
                    if existing is None:
                        existing = SkillOpinionOutcomeRecord(
                            sample_id=sample.id, horizon=horizon, engine_version=ENGINE_VERSION,
                        )
                        session.add(existing)
                        created += 1
                    existing.eval_status = status
                    existing.outcome = outcome
                    existing.direction_correct = correct
                    existing.analysis_date = sample.created_at.date()
                    existing.stock_return_pct = stock_return
                    existing.updated_at = datetime.now()
            session.commit()
        return {"created": created, "evaluated": completed, "pending": pending}

    @staticmethod
    def _evaluate(signal, bars, days):
        if len(bars) < days + 1 or not bars or not bars[0].close or not bars[-1].close:
            return "pending", None, None, None
        start, end = float(bars[0].close), float(bars[days].close)
        if start <= 0:
            return "pending", None, None, None
        stock_return = (end - start) / start * 100.0
        if signal == "hold":
            return "observational", "observational", None, stock_return
        directional = stock_return if signal in {"strong_buy", "buy"} else -stock_return
        correct = directional > 0
        return "evaluated", "hit" if correct else "miss", correct, stock_return
