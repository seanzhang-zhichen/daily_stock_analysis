"""初始化默认积分套餐数据

Revision ID: 20260603_seed_credit_packages
Revises: 20260603_credit_purchases
Create Date: 2026-06-03 16:10:00.000000+08:00
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260603_seed_credit_packages"
down_revision: Union[str, None] = "20260603_credit_purchases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 定义积分套餐表的反射结构，用于数据插入
app_credit_packages = sa.table(
    "app_credit_packages",
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("credit_amount", sa.Integer),
    sa.column("price_cents", sa.Integer),
    sa.column("currency", sa.String),
    sa.column("is_active", sa.Boolean),
    sa.column("sort_order", sa.Integer),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)


# 默认积分套餐配置列表
DEFAULT_CREDIT_PACKAGES = [
    {
        "code": "credits_200",
        "name": "200 积分包",
        "credit_amount": 200,
        "price_cents": 1990,
        "currency": "CNY",
        "is_active": True,
        "sort_order": 10,
    },
    {
        "code": "credits_1200",
        "name": "1200 积分包",
        "credit_amount": 1200,
        "price_cents": 9990,
        "currency": "CNY",
        "is_active": True,
        "sort_order": 20,
    },
    {
        "code": "credits_4000",
        "name": "4000 积分包",
        "credit_amount": 4000,
        "price_cents": 29900,
        "currency": "CNY",
        "is_active": True,
        "sort_order": 30,
    },
]


def upgrade() -> None:
    """升级：插入默认积分套餐数据，避免重复插入。"""
    bind = op.get_bind()
    now = datetime.utcnow()
    for package in DEFAULT_CREDIT_PACKAGES:
        # 检查该套餐是否已存在
        exists = bind.execute(
            sa.text("SELECT 1 FROM app_credit_packages WHERE code = :code"),
            {"code": package["code"]},
        ).first()
        if exists is not None:
            continue
        op.bulk_insert(
            app_credit_packages,
            [
                {
                    **package,
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        )


def downgrade() -> None:
    """降级：删除默认积分套餐数据。"""
    bind = op.get_bind()
    for package in DEFAULT_CREDIT_PACKAGES:
        bind.execute(
            sa.text("DELETE FROM app_credit_packages WHERE code = :code"),
            {"code": package["code"]},
        )
