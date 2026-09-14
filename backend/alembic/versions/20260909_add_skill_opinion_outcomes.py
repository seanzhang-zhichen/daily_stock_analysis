"""添加可归属专家意见样本和结果表

Revision ID: 20260909_skill_opinions
Revises: 20260908_signal_feedback
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260909_skill_opinions"
down_revision: Union[str, None] = "20260908_signal_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级：创建专家意见样本表和结果表，并建立相关索引。"""
    # 创建专家意见样本表，记录各技能对股票的分析意见
    op.create_table("skill_opinion_samples", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("analysis_history_id", sa.Integer(), nullable=False), sa.Column("stock_code", sa.String(16), nullable=False), sa.Column("skill_id", sa.String(128), nullable=False), sa.Column("signal", sa.String(16), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("analysis_history_id", "skill_id", name="uix_skill_opinion_sample"))
    # 创建专家意见样本表索引
    op.create_index("ix_skill_opinion_samples_history", "skill_opinion_samples", ["analysis_history_id"])
    op.create_index("ix_skill_opinion_samples_skill", "skill_opinion_samples", ["skill_id"])
    # 创建专家意见结果表，记录专家意见的后验评估结果
    op.create_table("skill_opinion_outcomes", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("sample_id", sa.Integer(), nullable=False), sa.Column("horizon", sa.String(16), nullable=False), sa.Column("engine_version", sa.String(32), nullable=False), sa.Column("eval_status", sa.String(24), nullable=False), sa.Column("outcome", sa.String(16)), sa.Column("direction_correct", sa.Boolean()), sa.Column("analysis_date", sa.Date()), sa.Column("stock_return_pct", sa.Float()), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("sample_id", "horizon", "engine_version", name="uix_skill_opinion_outcome"))
    # 创建专家意见结果表索引
    op.create_index("ix_skill_opinion_outcome_performance", "skill_opinion_outcomes", ["engine_version", "eval_status", "horizon"])


def downgrade() -> None:
    """降级：删除专家意见结果表和样本表。"""
    # 先删除专家意见结果表（存在外键依赖）
    op.drop_table("skill_opinion_outcomes")
    # 删除专家意见样本表
    op.drop_table("skill_opinion_samples")
