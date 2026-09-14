"""基于可归属专家结果的受限 Beta 先验权重计算服务。

通过统计各 skill 的历史命中情况，计算后验概率，并映射为权重因子，
用于在分析时对不同 skill 的意见进行加权。权重范围受限于
MIN_SKILL_OPINION_WEIGHT_FACTOR 和 MAX_SKILL_OPINION_WEIGHT_FACTOR，
防止极端值对整体结果的过度影响。
"""
import math
from sqlalchemy import case, func, select
from src.storage import DatabaseManager, SkillOpinionOutcomeRecord, SkillOpinionSampleRecord

# 最小样本量：低于此值时不计算权重，避免小样本偏差
MIN_SKILL_OUTCOME_SAMPLE_SIZE = 30
# 最大权重因子：限制权重上限，防止单一 skill 过度影响结果
MAX_SKILL_OPINION_WEIGHT_FACTOR = 1.2
# 最小权重因子：与上限对称，限制权重下限
MIN_SKILL_OPINION_WEIGHT_FACTOR = 1 / MAX_SKILL_OPINION_WEIGHT_FACTOR
# 引擎版本标识：用于区分不同版本的评估逻辑
ENGINE_VERSION = "skill-opinion-outcome-v1"


class SkillOpinionWeightService:
    """Skill 意见权重服务：基于历史命中统计计算各 skill 的权重因子。"""

    def __init__(self, db_manager=None):
        """初始化权重服务。

        Args:
            db_manager: 数据库管理器实例，默认使用全局单例。
        """
        self.db = db_manager or DatabaseManager.get_instance()

    def compute_weights(self, skill_ids):
        """计算给定 skill 列表的权重因子。

        对每个 skill，统计其历史样本数和命中数，应用 Beta(15, 15) 先验，
        计算后验概率，再通过 log-exp 映射转换为权重因子。

        Args:
            skill_ids: skill ID 列表。

        Returns:
            字典，键为 skill_id，值为对应的权重因子（默认 1.0）。
        """
        ids = list(dict.fromkeys(str(value).strip() for value in skill_ids if str(value).strip()))
        neutral = {value: 1.0 for value in ids}
        if not ids:
            return neutral
        try:
            with self.db.get_session() as session:
                # 查询各 skill 的样本总数和命中数
                rows = session.execute(
                    select(
                        SkillOpinionSampleRecord.skill_id,
                        func.count(SkillOpinionOutcomeRecord.id),
                        func.sum(case((SkillOpinionOutcomeRecord.outcome == "hit", 1), else_=0))
                    )
                    .join(SkillOpinionOutcomeRecord, SkillOpinionOutcomeRecord.sample_id == SkillOpinionSampleRecord.id)
                    .where(
                        SkillOpinionSampleRecord.skill_id.in_(ids),
                        SkillOpinionOutcomeRecord.engine_version == ENGINE_VERSION,
                        SkillOpinionOutcomeRecord.eval_status == "evaluated"
                    )
                    .group_by(SkillOpinionSampleRecord.skill_id)
                ).all()
            for skill_id, total, hits in rows:
                # 样本量不足时跳过，避免小样本偏差
                if total < MIN_SKILL_OUTCOME_SAMPLE_SIZE:
                    continue
                # Beta(15, 15) 先验 + 观测数据 = 后验概率
                posterior = (float(hits or 0) + 15.0) / (float(total) + 30.0)
                # 通过 log-exp 映射将后验概率转换为权重因子
                factor = math.exp(math.log(MAX_SKILL_OPINION_WEIGHT_FACTOR) * (2 * posterior - 1))
                # 限制权重因子在合理范围内
                neutral[skill_id] = max(MIN_SKILL_OPINION_WEIGHT_FACTOR, min(MAX_SKILL_OPINION_WEIGHT_FACTOR, factor))
        except Exception:
            # 异常时返回默认权重，保证服务可用性
            pass
        return neutral
