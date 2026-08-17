"""add persisted alert cooldown state

Revision ID: 20260814_alert_cooldowns
Revises: 20260814_decision_signals
Create Date: 2026-08-14 18:00:00+08:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260814_alert_cooldowns"
down_revision: Union[str, None] = "20260814_decision_signals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_cooldowns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("rule_key", sa.String(length=255), nullable=True),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="warning"),
        sa.Column("last_triggered_at", sa.DateTime(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_id", "target", "severity",
            name="uix_alert_cooldown_rule_target_severity",
        ),
    )
    op.create_index("ix_alert_cooldowns_rule_id", "alert_cooldowns", ["rule_id"])
    op.create_index("ix_alert_cooldowns_rule_key", "alert_cooldowns", ["rule_key"])
    op.create_index("ix_alert_cooldowns_target", "alert_cooldowns", ["target"])
    op.create_index("ix_alert_cooldowns_severity", "alert_cooldowns", ["severity"])
    op.create_index("ix_alert_cooldowns_last_triggered_at", "alert_cooldowns", ["last_triggered_at"])
    op.create_index("ix_alert_cooldowns_cooldown_until", "alert_cooldowns", ["cooldown_until"])
    op.create_index("ix_alert_cooldowns_state", "alert_cooldowns", ["state"])
    op.create_index("ix_alert_cooldowns_updated_at", "alert_cooldowns", ["updated_at"])


def downgrade() -> None:
    for name in (
        "ix_alert_cooldowns_updated_at",
        "ix_alert_cooldowns_state",
        "ix_alert_cooldowns_cooldown_until",
        "ix_alert_cooldowns_last_triggered_at",
        "ix_alert_cooldowns_severity",
        "ix_alert_cooldowns_target",
        "ix_alert_cooldowns_rule_key",
        "ix_alert_cooldowns_rule_id",
    ):
        op.drop_index(name, table_name="alert_cooldowns")
    op.drop_table("alert_cooldowns")
