"""add stable canonical identity to stock daily rows

Revision ID: 20260904_stock_daily_canonical
Revises: 20260814_alert_cooldowns
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260904_stock_daily_canonical"
down_revision: Union[str, None] = "20260814_alert_cooldowns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stock_daily", sa.Column("canonical_id", sa.String(length=32), nullable=True))
    op.create_index("ix_stock_daily_canonical_id", "stock_daily", ["canonical_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_stock_daily_canonical_id", table_name="stock_daily")
    op.drop_column("stock_daily", "canonical_id")
