"""add user feedback for decision signals"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260908_signal_feedback"
down_revision: Union[str, None] = "20260907_intelligence_pool"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "decision_signal_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("feedback_value", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_id", "user_id", name="uix_decision_signal_feedback_owner"),
    )
    op.create_index("ix_decision_signal_feedback_signal_id", "decision_signal_feedback", ["signal_id"])
    op.create_index("ix_decision_signal_feedback_user_id", "decision_signal_feedback", ["user_id"])

def downgrade() -> None:
    op.drop_table("decision_signal_feedback")
