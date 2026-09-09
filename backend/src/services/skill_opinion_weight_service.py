"""Bounded Beta-prior weights from attributable specialist outcomes."""
import math
from sqlalchemy import case, func, select
from src.storage import DatabaseManager, SkillOpinionOutcomeRecord, SkillOpinionSampleRecord
MIN_SKILL_OUTCOME_SAMPLE_SIZE = 30
MAX_SKILL_OPINION_WEIGHT_FACTOR = 1.2
MIN_SKILL_OPINION_WEIGHT_FACTOR = 1 / MAX_SKILL_OPINION_WEIGHT_FACTOR
ENGINE_VERSION = "skill-opinion-outcome-v1"
class SkillOpinionWeightService:
    def __init__(self, db_manager=None): self.db = db_manager or DatabaseManager.get_instance()
    def compute_weights(self, skill_ids):
        ids = list(dict.fromkeys(str(value).strip() for value in skill_ids if str(value).strip())); neutral = {value: 1.0 for value in ids}
        if not ids: return neutral
        try:
            with self.db.get_session() as session:
                rows = session.execute(select(SkillOpinionSampleRecord.skill_id, func.count(SkillOpinionOutcomeRecord.id), func.sum(case((SkillOpinionOutcomeRecord.outcome == "hit", 1), else_=0))).join(SkillOpinionOutcomeRecord, SkillOpinionOutcomeRecord.sample_id == SkillOpinionSampleRecord.id).where(SkillOpinionSampleRecord.skill_id.in_(ids), SkillOpinionOutcomeRecord.engine_version == ENGINE_VERSION, SkillOpinionOutcomeRecord.eval_status == "evaluated").group_by(SkillOpinionSampleRecord.skill_id)).all()
            for skill_id, total, hits in rows:
                if total < MIN_SKILL_OUTCOME_SAMPLE_SIZE: continue
                posterior = (float(hits or 0) + 15.0) / (float(total) + 30.0)
                factor = math.exp(math.log(MAX_SKILL_OPINION_WEIGHT_FACTOR) * (2 * posterior - 1))
                neutral[skill_id] = max(MIN_SKILL_OPINION_WEIGHT_FACTOR, min(MAX_SKILL_OPINION_WEIGHT_FACTOR, factor))
        except Exception: pass
        return neutral
