"""添加本地情报池表

Revision ID: 20260907_intelligence_pool
Revises: 20260907_conversation_state
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260907_intelligence_pool"
down_revision: Union[str, None] = "20260907_conversation_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级：创建情报来源表和情报条目表，并建立相关索引。"""
    inspector = sa.inspect(op.get_bind())
    # 创建情报来源表
    if "intelligence_sources" not in inspector.get_table_names():
        op.create_table("intelligence_sources",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(100), nullable=False),
            sa.Column("source_type", sa.String(32), nullable=False), sa.Column("url", sa.String(1000), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("scope_type", sa.String(32), nullable=False),
            sa.Column("scope_value", sa.String(64)), sa.Column("market", sa.String(32), nullable=False), sa.Column("description", sa.Text()),
            sa.Column("last_status", sa.String(32)), sa.Column("last_error", sa.Text()), sa.Column("last_fetched_at", sa.DateTime()),
            sa.Column("created_at", sa.DateTime()), sa.Column("updated_at", sa.DateTime()), sa.UniqueConstraint("name"))
    inspector = sa.inspect(op.get_bind())
    # 创建情报来源表相关索引
    for name, columns in (("ix_intelligence_sources_market", ["market"]), ("ix_intelligence_sources_enabled", ["enabled"])):
        if name not in {idx["name"] for idx in inspector.get_indexes("intelligence_sources")}:
            op.create_index(name, "intelligence_sources", columns)
    # 创建情报条目表
    if "intelligence_items" not in inspector.get_table_names():
        op.create_table("intelligence_items",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source_id", sa.Integer(), sa.ForeignKey("intelligence_sources.id", ondelete="SET NULL")),
        sa.Column("source_name", sa.String(100)), sa.Column("source_type", sa.String(32), nullable=False), sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text()), sa.Column("url", sa.String(1000), nullable=False), sa.Column("source", sa.String(100)),
        sa.Column("published_at", sa.DateTime()), sa.Column("fetched_at", sa.DateTime()), sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("scope_value", sa.String(64), nullable=False), sa.Column("market", sa.String(32), nullable=False), sa.Column("raw_payload", sa.Text()),
            )
    inspector = sa.inspect(op.get_bind())
    indexes = {idx["name"] for idx in inspector.get_indexes("intelligence_items")}
    # 创建情报条目表唯一索引（对长 URL 列使用前缀索引，以保持在 utf8mb4 的 3072 字节键限制内）
    if "uix_intel_item_source_scope_url" not in indexes:
        op.create_index("uix_intel_item_source_scope_url", "intelligence_items", ["source_id", "url", "scope_type", "scope_value", "market"], unique=True, mysql_length={"url": 191})
    # 创建情报条目表其他索引
    if "ix_intel_item_scope_time" not in indexes:
        op.create_index("ix_intel_item_scope_time", "intelligence_items", ["scope_type", "scope_value", "market", "published_at"])
    if "ix_intel_item_fetch_time" not in indexes:
        op.create_index("ix_intel_item_fetch_time", "intelligence_items", ["fetched_at"])


def downgrade() -> None:
    """降级：删除情报条目表和情报来源表。"""
    # 先删除情报条目表（存在外键依赖）
    op.drop_table("intelligence_items")
    # 删除情报来源表
    op.drop_table("intelligence_sources")
