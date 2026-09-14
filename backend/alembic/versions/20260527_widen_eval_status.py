"""扩展 backtest_results.eval_status 字段长度至 String(32)

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
    """升级：将 backtest_results 表的 eval_status 字段长度从 16 扩展至 32。"""
    with op.batch_alter_table("backtest_results") as batch_op:
        batch_op.alter_column(
            "eval_status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )


def downgrade() -> None:
    """降级：将 backtest_results 表的 eval_status 字段长度从 32 恢复至 16。"""
    with op.batch_alter_table("backtest_results") as batch_op:
        batch_op.alter_column(
            "eval_status",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
            existing_nullable=False,
        )
