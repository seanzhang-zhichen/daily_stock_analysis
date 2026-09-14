"""添加用户个人资料字段

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
    """升级：为 app_users 表添加 display_name 和 avatar_url 字段。"""
    # 添加用户显示名称字段
    op.add_column("app_users", sa.Column("display_name", sa.String(length=64), nullable=True))
    # 添加用户头像 URL 字段
    op.add_column("app_users", sa.Column("avatar_url", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    """降级：删除 app_users 表的 avatar_url 和 display_name 字段。"""
    # 删除用户头像 URL 字段
    op.drop_column("app_users", "avatar_url")
    # 删除用户显示名称字段
    op.drop_column("app_users", "display_name")
