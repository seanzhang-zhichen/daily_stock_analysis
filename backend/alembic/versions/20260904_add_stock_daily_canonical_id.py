"""为 stock_daily 表添加稳定规范身份标识

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
    """升级：为 stock_daily 表添加 canonical_id 字段并创建索引。"""
    # 为 stock_daily 表添加规范身份标识字段
    op.add_column("stock_daily", sa.Column("canonical_id", sa.String(length=32), nullable=True))
    # 创建 canonical_id 字段索引
    op.create_index("ix_stock_daily_canonical_id", "stock_daily", ["canonical_id"], unique=False)


def downgrade() -> None:
    """降级：删除 stock_daily 表的 canonical_id 字段及其索引。"""
    # 删除 canonical_id 字段索引
    op.drop_index("ix_stock_daily_canonical_id", table_name="stock_daily")
    # 删除 canonical_id 字段
    op.drop_column("stock_daily", "canonical_id")
