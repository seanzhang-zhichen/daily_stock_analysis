"""add user profile fields

Revision ID: 20260604_user_profile
Revises: 20260604_research_reports
Create Date: 2026-06-04 19:30:00.000000+08:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260604_user_profile"
down_revision: Union[str, None] = "20260604_research_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("display_name", sa.String(length=64), nullable=True))
    op.add_column("app_users", sa.Column("avatar_url", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("app_users", "avatar_url")
    op.drop_column("app_users", "display_name")
