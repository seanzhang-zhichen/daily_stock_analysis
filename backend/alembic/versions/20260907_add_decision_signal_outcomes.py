"""add A-share decision signal posterior outcomes

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
    op.create_table("decision_signal_outcomes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False), sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("engine_version", sa.String(32), nullable=False), sa.Column("eval_status", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(16)), sa.Column("direction_expected", sa.String(16)), sa.Column("direction_correct", sa.Boolean()),
        sa.Column("unable_reason", sa.String(64)), sa.Column("anchor_date", sa.Date()), sa.Column("eval_window_days", sa.Integer()),
        sa.Column("start_price", sa.Float()), sa.Column("end_close", sa.Float()), sa.Column("max_high", sa.Float()), sa.Column("min_low", sa.Float()), sa.Column("stock_return_pct", sa.Float()),
        sa.Column("action", sa.String(16)), sa.Column("market", sa.String(8)), sa.Column("holding_state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False), sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_id", "horizon", "engine_version", name="uix_decision_signal_outcome_key"))
    for name, columns in (("ix_decision_signal_outcomes_signal_id", ["signal_id"]), ("ix_decision_signal_outcomes_horizon", ["horizon"]), ("ix_decision_signal_outcomes_engine_version", ["engine_version"]), ("ix_decision_signal_outcomes_eval_status", ["eval_status"]), ("ix_decision_signal_outcome_stats_action", ["engine_version", "action", "horizon"])):
        op.create_index(name, "decision_signal_outcomes", columns, unique=False)


def downgrade() -> None:
    op.drop_table("decision_signal_outcomes")
