"""添加滚动会话摘要表

Revision ID: 20260907_conversation_summaries
Revises: 20260907_decision_outcomes
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260907_conversation_summaries"
down_revision: Union[str, None] = "20260907_decision_outcomes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级：创建 conversation_summaries 表并建立相关索引。"""
    # 创建会话摘要表，用于存储对话的历史摘要信息
    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(100), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("covered_message_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("session_id"),
    )
    # 创建会话摘要表相关索引
    op.create_index("ix_conversation_summaries_session_id", "conversation_summaries", ["session_id"], unique=False)
    op.create_index("ix_conversation_summaries_created_at", "conversation_summaries", ["created_at"], unique=False)
    op.create_index("ix_conversation_summaries_updated_at", "conversation_summaries", ["updated_at"], unique=False)


def downgrade() -> None:
    """降级：删除 conversation_summaries 表。"""
    # 删除会话摘要表（索引会随表一起删除）
    op.drop_table("conversation_summaries")
