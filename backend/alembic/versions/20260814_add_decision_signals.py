"""add user-owned AI decision signals

Revision ID: 20260814_decision_signals
Revises: 20260814_screening_runs
Create Date: 2026-08-14 17:30:00.000000+08:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260814_decision_signals"
down_revision: Union[str, None] = "20260814_screening_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("stock_code", sa.String(length=32), nullable=False),
        sa.Column("stock_name", sa.String(length=64), nullable=True),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("source_report_id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("trigger_source", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("action_label", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("horizon", sa.String(length=16), nullable=True),
        sa.Column("entry_low", sa.Float(), nullable=True),
        sa.Column("entry_high", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("target_price", sa.Float(), nullable=True),
        sa.Column("invalidation", sa.Text(), nullable=True),
        sa.Column("watch_conditions", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("risk_summary", sa.Text(), nullable=True),
        sa.Column("catalyst_summary", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=True),
        sa.Column("data_quality_json", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("plan_quality", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source_type",
            "source_report_id",
            name="uix_decision_signal_owner_source",
        ),
    )
    for name, columns in (
        ("ix_decision_signals_user_id", ["user_id"]),
        ("ix_decision_signals_stock_code", ["stock_code"]),
        ("ix_decision_signals_market", ["market"]),
        ("ix_decision_signals_source_type", ["source_type"]),
        ("ix_decision_signals_source_report_id", ["source_report_id"]),
        ("ix_decision_signals_trace_id", ["trace_id"]),
        ("ix_decision_signals_action", ["action"]),
        ("ix_decision_signals_horizon", ["horizon"]),
        ("ix_decision_signals_status", ["status"]),
        ("ix_decision_signals_expires_at", ["expires_at"]),
        ("ix_decision_signals_created_at", ["created_at"]),
        (
            "ix_decision_signal_owner_stock_status_time",
            ["user_id", "stock_code", "status", "created_at"],
        ),
        (
            "ix_decision_signal_owner_market_action_time",
            ["user_id", "market", "action", "created_at"],
        ),
    ):
        op.create_index(name, "decision_signals", columns, unique=False)


def downgrade() -> None:
    op.drop_table("decision_signals")
