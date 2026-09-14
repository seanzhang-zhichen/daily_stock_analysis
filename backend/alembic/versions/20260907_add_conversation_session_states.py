"""添加会话会话状态表

Revision ID: 20260907_conversation_state
Revises: 20260907_conversation_summaries
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260907_conversation_state"
down_revision: Union[str, None] = "20260907_conversation_summaries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级：创建 conversation_session_states 表。

    MySQL DDL 是非事务性的。之前的启动中断或并发启动可能会在 Alembic 记录
    此版本之前留下该表；保留现有数据并让 Alembic 推进版本。
    """
    # 检查表是否已存在（处理 MySQL 非事务性 DDL 可能导致的重复创建问题）
    if "conversation_session_states" not in sa.inspect(op.get_bind()).get_table_names():
        # 创建会话状态表，用于存储用户在会话中选择的技能 ID 等状态信息
        op.create_table(
            "conversation_session_states",
            sa.Column("session_id", sa.String(100), nullable=False),
            sa.Column("selected_skill_ids_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("session_id"),
        )


def downgrade() -> None:
    """降级：删除 conversation_session_states 表。"""
    # 删除会话状态表
    op.drop_table("conversation_session_states")
