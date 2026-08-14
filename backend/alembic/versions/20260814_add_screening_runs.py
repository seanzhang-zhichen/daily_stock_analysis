"""add persisted stock screening runs

Revision ID: 20260814_screening_runs
Revises: 20260604_user_profile
Create Date: 2026-08-14 15:10:00.000000+08:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260814_screening_runs"
down_revision: Union[str, None] = "20260604_user_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "screening_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("snapshot_source", sa.String(length=64), nullable=True),
        sa.Column("snapshot_count", sa.Integer(), nullable=True),
        sa.Column("after_filter_count", sa.Integer(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_ranked", sa.Boolean(), nullable=True),
        sa.Column("daily_enriched", sa.Boolean(), nullable=True),
        sa.Column("source_errors_json", sa.Text(), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("ix_screening_runs_user_id", "screening_runs", ["user_id"], unique=False)
    op.create_index("ix_screening_runs_run_id", "screening_runs", ["run_id"], unique=True)
    op.create_index("ix_screening_runs_strategy", "screening_runs", ["strategy"], unique=False)
    op.create_index("ix_screening_runs_market", "screening_runs", ["market"], unique=False)
    op.create_index("ix_screening_runs_snapshot_source", "screening_runs", ["snapshot_source"], unique=False)
    op.create_index("ix_screening_runs_created_at", "screening_runs", ["created_at"], unique=False)
    op.create_index(
        "ix_screening_run_strategy_created",
        "screening_runs",
        ["strategy", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_screening_run_market_created",
        "screening_runs",
        ["market", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_screening_run_market_created", table_name="screening_runs")
    op.drop_index("ix_screening_run_strategy_created", table_name="screening_runs")
    op.drop_index("ix_screening_runs_created_at", table_name="screening_runs")
    op.drop_index("ix_screening_runs_snapshot_source", table_name="screening_runs")
    op.drop_index("ix_screening_runs_market", table_name="screening_runs")
    op.drop_index("ix_screening_runs_strategy", table_name="screening_runs")
    op.drop_index("ix_screening_runs_run_id", table_name="screening_runs")
    op.drop_index("ix_screening_runs_user_id", table_name="screening_runs")
    op.drop_table("screening_runs")
