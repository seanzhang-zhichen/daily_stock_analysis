"""add conversation session state"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260907_conversation_state"
down_revision: Union[str, None] = "20260907_conversation_summaries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MySQL DDL is non-transactional.  A previous interrupted/concurrent
    # startup can therefore leave the table present before Alembic records
    # this revision; preserve that data and let Alembic advance the revision.
    if "conversation_session_states" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "conversation_session_states",
            sa.Column("session_id", sa.String(100), nullable=False),
            sa.Column("selected_skill_ids_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("session_id"),
        )


def downgrade() -> None:
    op.drop_table("conversation_session_states")
