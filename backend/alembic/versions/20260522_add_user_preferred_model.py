"""remove_byok_add_user_preferred_model

Revision ID: 20260522_pref_model
Revises: b0bc3c721ef0
Create Date: 2026-05-22 15:45:00.000000+08:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260522_pref_model"
down_revision: Union[str, None] = "b0bc3c721ef0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("preferred_model", sa.String(length=128), nullable=True))
    op.drop_table("app_user_byok_credentials")
    op.drop_column("app_plans", "can_byok")


def downgrade() -> None:
    op.add_column("app_plans", sa.Column("can_byok", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "app_user_byok_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uix_app_user_byok_user_provider"),
    )
    op.create_index(op.f("ix_app_user_byok_credentials_user_id"), "app_user_byok_credentials", ["user_id"], unique=False)
    op.create_index(op.f("ix_app_user_byok_credentials_provider"), "app_user_byok_credentials", ["provider"], unique=False)
    op.drop_column("app_users", "preferred_model")
