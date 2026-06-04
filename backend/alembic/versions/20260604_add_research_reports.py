"""add research reports

Revision ID: 20260604_research_reports
Revises: 20260603_seed_credit_packages
Create Date: 2026-06-04 10:00:00.000000+08:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260604_research_reports"
down_revision: Union[str, None] = "20260603_seed_credit_packages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    if not _table_exists(inspector, table_name):
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if not _table_exists(inspector, table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _refresh_inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _create_index_if_missing(
    inspector: sa.Inspector,
    index_name: str,
    table_name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> sa.Inspector:
    if not _index_exists(inspector, table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)
        return _refresh_inspector()
    return inspector


def _drop_index_if_exists(inspector: sa.Inspector, index_name: str, table_name: str) -> sa.Inspector:
    if _index_exists(inspector, table_name, index_name):
        op.drop_index(index_name, table_name=table_name)
        return _refresh_inspector()
    return inspector


def upgrade() -> None:
    inspector = _refresh_inspector()

    if _table_exists(inspector, "app_users") and not _column_exists(inspector, "app_users", "is_research_operator"):
        op.add_column(
            "app_users",
            sa.Column("is_research_operator", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        inspector = _refresh_inspector()
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_users_is_research_operator"),
        "app_users",
        ["is_research_operator"],
    )

    if not _table_exists(inspector, "app_research_reports"):
        op.create_table(
            "app_research_reports",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("preview_content", sa.Text(), nullable=False),
            sa.Column("full_content", sa.Text(), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=True),
            sa.Column("tags", sa.Text(), nullable=True),
            sa.Column("cover_image_url", sa.String(length=1024), nullable=True),
            sa.Column("price_credits", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("author_id", sa.Integer(), nullable=True),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["author_id"], ["app_users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = _refresh_inspector()
    for name, columns, unique in [
        (op.f("ix_app_research_reports_author_id"), ["author_id"], False),
        (op.f("ix_app_research_reports_category"), ["category"], False),
        (op.f("ix_app_research_reports_created_at"), ["created_at"], False),
        (op.f("ix_app_research_reports_is_published"), ["is_published"], False),
        (op.f("ix_app_research_reports_published_at"), ["published_at"], False),
        ("ix_app_research_reports_published_time", ["is_published", "published_at"], False),
    ]:
        inspector = _create_index_if_missing(inspector, name, "app_research_reports", columns, unique=unique)

    if not _table_exists(inspector, "app_research_report_purchases"):
        op.create_table(
            "app_research_report_purchases",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("report_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("price_credits", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ledger_id", sa.Integer(), nullable=True),
            sa.Column("purchased_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["ledger_id"], ["app_credit_ledger.id"]),
            sa.ForeignKeyConstraint(["report_id"], ["app_research_reports.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("report_id", "user_id", name="uix_app_research_purchase_report_user"),
        )
        inspector = _refresh_inspector()
    for name, columns in [
        (op.f("ix_app_research_report_purchases_report_id"), ["report_id"]),
        (op.f("ix_app_research_report_purchases_user_id"), ["user_id"]),
        (op.f("ix_app_research_report_purchases_purchased_at"), ["purchased_at"]),
    ]:
        inspector = _create_index_if_missing(inspector, name, "app_research_report_purchases", columns)

    if not _table_exists(inspector, "app_research_report_reactions"):
        op.create_table(
            "app_research_report_reactions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("report_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("reaction", sa.String(length=8), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["report_id"], ["app_research_reports.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("report_id", "user_id", name="uix_app_research_reaction_report_user"),
        )
        inspector = _refresh_inspector()
    for name, columns in [
        (op.f("ix_app_research_report_reactions_report_id"), ["report_id"]),
        (op.f("ix_app_research_report_reactions_user_id"), ["user_id"]),
        (op.f("ix_app_research_report_reactions_reaction"), ["reaction"]),
    ]:
        inspector = _create_index_if_missing(inspector, name, "app_research_report_reactions", columns)

    if not _table_exists(inspector, "app_research_report_comments"):
        op.create_table(
            "app_research_report_comments",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("report_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="visible"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["report_id"], ["app_research_reports.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = _refresh_inspector()
    for name, columns in [
        (op.f("ix_app_research_report_comments_report_id"), ["report_id"]),
        (op.f("ix_app_research_report_comments_user_id"), ["user_id"]),
        (op.f("ix_app_research_report_comments_status"), ["status"]),
        (op.f("ix_app_research_report_comments_created_at"), ["created_at"]),
        ("ix_app_research_comments_report_created", ["report_id", "created_at"]),
    ]:
        _create_index_if_missing(inspector, name, "app_research_report_comments", columns)


def downgrade() -> None:
    inspector = _refresh_inspector()

    for name in [
        "ix_app_research_comments_report_created",
        op.f("ix_app_research_report_comments_created_at"),
        op.f("ix_app_research_report_comments_status"),
        op.f("ix_app_research_report_comments_user_id"),
        op.f("ix_app_research_report_comments_report_id"),
    ]:
        inspector = _drop_index_if_exists(inspector, name, "app_research_report_comments")
    if _table_exists(inspector, "app_research_report_comments"):
        op.drop_table("app_research_report_comments")
        inspector = _refresh_inspector()

    for name in [
        op.f("ix_app_research_report_reactions_reaction"),
        op.f("ix_app_research_report_reactions_user_id"),
        op.f("ix_app_research_report_reactions_report_id"),
    ]:
        inspector = _drop_index_if_exists(inspector, name, "app_research_report_reactions")
    if _table_exists(inspector, "app_research_report_reactions"):
        op.drop_table("app_research_report_reactions")
        inspector = _refresh_inspector()

    for name in [
        op.f("ix_app_research_report_purchases_purchased_at"),
        op.f("ix_app_research_report_purchases_user_id"),
        op.f("ix_app_research_report_purchases_report_id"),
    ]:
        inspector = _drop_index_if_exists(inspector, name, "app_research_report_purchases")
    if _table_exists(inspector, "app_research_report_purchases"):
        op.drop_table("app_research_report_purchases")
        inspector = _refresh_inspector()

    for name in [
        "ix_app_research_reports_published_time",
        op.f("ix_app_research_reports_published_at"),
        op.f("ix_app_research_reports_is_published"),
        op.f("ix_app_research_reports_created_at"),
        op.f("ix_app_research_reports_category"),
        op.f("ix_app_research_reports_author_id"),
    ]:
        inspector = _drop_index_if_exists(inspector, name, "app_research_reports")
    if _table_exists(inspector, "app_research_reports"):
        op.drop_table("app_research_reports")

    inspector = _refresh_inspector()
    inspector = _drop_index_if_exists(
        inspector,
        op.f("ix_app_users_is_research_operator"),
        "app_users",
    )
    if _column_exists(inspector, "app_users", "is_research_operator"):
        op.drop_column("app_users", "is_research_operator")
