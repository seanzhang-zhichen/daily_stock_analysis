"""add_stock_index_tables

Revision ID: 20260523_stock_index
Revises: 20260522_pref_model
Create Date: 2026-05-23 15:10:00.000000+08:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260523_stock_index"
down_revision: Union[str, None] = "20260522_pref_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_index",
        sa.Column("canonical_code", sa.String(length=32), nullable=False),
        sa.Column("display_code", sa.String(length=32), nullable=False),
        sa.Column("name_zh", sa.String(length=128), nullable=False),
        sa.Column("pinyin_full", sa.String(length=255), nullable=True),
        sa.Column("pinyin_abbr", sa.String(length=64), nullable=True),
        sa.Column("aliases", sa.Text(), nullable=True),
        sa.Column("aliases_text", sa.Text(), nullable=True),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("popularity", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("canonical_code"),
    )
    op.create_index(op.f("ix_stock_index_active"), "stock_index", ["active"], unique=False)
    op.create_index(op.f("ix_stock_index_asset_type"), "stock_index", ["asset_type"], unique=False)
    op.create_index(op.f("ix_stock_index_display_code"), "stock_index", ["display_code"], unique=False)
    op.create_index("ix_stock_index_display_active", "stock_index", ["display_code", "active"], unique=False)
    op.create_index(op.f("ix_stock_index_market"), "stock_index", ["market"], unique=False)
    op.create_index(op.f("ix_stock_index_name_zh"), "stock_index", ["name_zh"], unique=False)
    op.create_index("ix_stock_index_name_active", "stock_index", ["name_zh", "active"], unique=False)
    op.create_index(op.f("ix_stock_index_pinyin_abbr"), "stock_index", ["pinyin_abbr"], unique=False)
    op.create_index(op.f("ix_stock_index_pinyin_full"), "stock_index", ["pinyin_full"], unique=False)
    op.create_index("ix_stock_index_pinyin_active", "stock_index", ["pinyin_abbr", "pinyin_full", "active"], unique=False)
    op.create_index(op.f("ix_stock_index_updated_at"), "stock_index", ["updated_at"], unique=False)

    op.create_table(
        "stock_index_meta",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(op.f("ix_stock_index_meta_updated_at"), "stock_index_meta", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_stock_index_meta_updated_at"), table_name="stock_index_meta")
    op.drop_table("stock_index_meta")

    op.drop_index(op.f("ix_stock_index_updated_at"), table_name="stock_index")
    op.drop_index("ix_stock_index_pinyin_active", table_name="stock_index")
    op.drop_index(op.f("ix_stock_index_pinyin_full"), table_name="stock_index")
    op.drop_index(op.f("ix_stock_index_pinyin_abbr"), table_name="stock_index")
    op.drop_index("ix_stock_index_name_active", table_name="stock_index")
    op.drop_index(op.f("ix_stock_index_name_zh"), table_name="stock_index")
    op.drop_index(op.f("ix_stock_index_market"), table_name="stock_index")
    op.drop_index("ix_stock_index_display_active", table_name="stock_index")
    op.drop_index(op.f("ix_stock_index_display_code"), table_name="stock_index")
    op.drop_index(op.f("ix_stock_index_asset_type"), table_name="stock_index")
    op.drop_index(op.f("ix_stock_index_active"), table_name="stock_index")
    op.drop_table("stock_index")
