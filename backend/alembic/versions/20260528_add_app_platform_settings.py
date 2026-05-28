"""add app platform settings

Revision ID: 20260528_add_app_platform_settings
Revises: 20260528_seed_free_plan
Create Date: 2026-05-28 12:20:00.000000+08:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260528_add_app_platform_settings"
down_revision: Union[str, None] = "20260528_seed_free_plan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_platform_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["app_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_platform_settings_key"), "app_platform_settings", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_app_platform_settings_key"), table_name="app_platform_settings")
    op.drop_table("app_platform_settings")
