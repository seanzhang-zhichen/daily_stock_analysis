"""widen backtest_results.eval_status to String(32)

Revision ID: 20260527_widen_eval_status
Revises: 20260523_stock_index
Create Date: 2026-05-27 15:00:00.000000+08:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260527_widen_eval_status"
down_revision: Union[str, None] = "20260523_stock_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'backtest_results',
        'eval_status',
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'backtest_results',
        'eval_status',
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
