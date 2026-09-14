"""添加A股决策信号后验结果表

Revision ID: 20260907_decision_outcomes
Revises: 20260904_stock_daily_canonical
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260907_decision_outcomes"
down_revision: Union[str, None] = "20260904_stock_daily_canonical"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级：创建 decision_signal_outcomes 表并建立相关索引。"""
    # 创建决策信号后验结果表，用于记录决策信号的实际表现
    op.create_table("decision_signal_outcomes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column("eval_status", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(16)),
        sa.Column("direction_expected", sa.String(16)),
        sa.Column("direction_correct", sa.Boolean()),
        sa.Column("unable_reason", sa.String(64)),
        sa.Column("anchor_date", sa.Date()),
        sa.Column("eval_window_days", sa.Integer()),
        sa.Column("start_price", sa.Float()),
        sa.Column("end_close", sa.Float()),
        sa.Column("max_high", sa.Float()),
        sa.Column("min_low", sa.Float()),
        sa.Column("stock_return_pct", sa.Float()),
        sa.Column("action", sa.String(16)),
        sa.Column("market", sa.String(8)),
        sa.Column("holding_state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_id", "horizon", "engine_version", name="uix_decision_signal_outcome_key"))
    # 创建决策信号后验结果表相关索引
    for name, columns in (
        ("ix_decision_signal_outcomes_signal_id", ["signal_id"]),
        ("ix_decision_signal_outcomes_horizon", ["horizon"]),
        ("ix_decision_signal_outcomes_engine_version", ["engine_version"]),
        ("ix_decision_signal_outcomes_eval_status", ["eval_status"]),
        ("ix_decision_signal_outcome_stats_action", ["engine_version", "action", "horizon"]),
    ):
        op.create_index(name, "decision_signal_outcomes", columns, unique=False)


def downgrade() -> None:
    """降级：删除 decision_signal_outcomes 表。"""
    # 删除决策信号后验结果表（索引会随表一起删除）
    op.drop_table("decision_signal_outcomes")
