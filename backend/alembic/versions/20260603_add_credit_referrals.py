"""add credit and referral tables

Revision ID: 20260603_credit_referrals
Revises: 20260528_platform_settings
Create Date: 2026-06-03 10:00:00.000000+08:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260603_credit_referrals"
down_revision: Union[str, None] = "20260528_platform_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if not _table_exists(inspector, table_name):
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    if not _table_exists(inspector, table_name):
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


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

    if not _column_exists(inspector, "app_users", "credit_balance"):
        op.add_column(
            "app_users",
            sa.Column("credit_balance", sa.Integer(), nullable=False, server_default="0"),
        )
        inspector = _refresh_inspector()
    if not _column_exists(inspector, "app_users", "referral_code"):
        op.add_column("app_users", sa.Column("referral_code", sa.String(length=32), nullable=True))
        inspector = _refresh_inspector()
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_users_referral_code"),
        "app_users",
        ["referral_code"],
        unique=True,
    )

    if not _table_exists(inspector, "app_user_referrals"):
        op.create_table(
            "app_user_referrals",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("inviter_user_id", sa.Integer(), nullable=False),
            sa.Column("invitee_user_id", sa.Integer(), nullable=False),
            sa.Column("invite_code", sa.String(length=64), nullable=True),
            sa.Column("registered_at", sa.DateTime(), nullable=False),
            sa.Column("signup_reward_credited_at", sa.DateTime(), nullable=True),
            sa.Column("first_paid_order_no", sa.String(length=32), nullable=True),
            sa.Column("first_paid_at", sa.DateTime(), nullable=True),
            sa.Column("paid_reward_credited_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["invitee_user_id"], ["app_users.id"]),
            sa.ForeignKeyConstraint(["inviter_user_id"], ["app_users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = _refresh_inspector()
    inspector = _create_index_if_missing(inspector, op.f("ix_app_user_referrals_first_paid_at"), "app_user_referrals", ["first_paid_at"])
    inspector = _create_index_if_missing(inspector, op.f("ix_app_user_referrals_first_paid_order_no"), "app_user_referrals", ["first_paid_order_no"])
    inspector = _create_index_if_missing(inspector, op.f("ix_app_user_referrals_invite_code"), "app_user_referrals", ["invite_code"])
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_user_referrals_invitee_user_id"),
        "app_user_referrals",
        ["invitee_user_id"],
        unique=True,
    )
    inspector = _create_index_if_missing(inspector, op.f("ix_app_user_referrals_inviter_user_id"), "app_user_referrals", ["inviter_user_id"])
    inspector = _create_index_if_missing(inspector, op.f("ix_app_user_referrals_registered_at"), "app_user_referrals", ["registered_at"])

    if not _table_exists(inspector, "app_credit_ledger"):
        op.create_table(
            "app_credit_ledger",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("delta", sa.Integer(), nullable=False),
            sa.Column("balance_after", sa.Integer(), nullable=False),
            sa.Column("reason", sa.String(length=32), nullable=False),
            sa.Column("related_type", sa.String(length=32), nullable=True),
            sa.Column("related_id", sa.String(length=128), nullable=True),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("note", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = _refresh_inspector()
    inspector = _create_index_if_missing(inspector, op.f("ix_app_credit_ledger_created_at"), "app_credit_ledger", ["created_at"])
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_credit_ledger_idempotency_key"),
        "app_credit_ledger",
        ["idempotency_key"],
        unique=True,
    )
    inspector = _create_index_if_missing(inspector, op.f("ix_app_credit_ledger_reason"), "app_credit_ledger", ["reason"])
    inspector = _create_index_if_missing(inspector, op.f("ix_app_credit_ledger_related_id"), "app_credit_ledger", ["related_id"])
    inspector = _create_index_if_missing(inspector, op.f("ix_app_credit_ledger_related_type"), "app_credit_ledger", ["related_type"])
    _create_index_if_missing(inspector, op.f("ix_app_credit_ledger_user_id"), "app_credit_ledger", ["user_id"])


def downgrade() -> None:
    inspector = _refresh_inspector()

    inspector = _drop_index_if_exists(inspector, op.f("ix_app_credit_ledger_user_id"), "app_credit_ledger")
    inspector = _drop_index_if_exists(inspector, op.f("ix_app_credit_ledger_related_type"), "app_credit_ledger")
    inspector = _drop_index_if_exists(inspector, op.f("ix_app_credit_ledger_related_id"), "app_credit_ledger")
    inspector = _drop_index_if_exists(inspector, op.f("ix_app_credit_ledger_reason"), "app_credit_ledger")
    inspector = _drop_index_if_exists(inspector, op.f("ix_app_credit_ledger_idempotency_key"), "app_credit_ledger")
    inspector = _drop_index_if_exists(inspector, op.f("ix_app_credit_ledger_created_at"), "app_credit_ledger")
    if _table_exists(inspector, "app_credit_ledger"):
        op.drop_table("app_credit_ledger")
        inspector = _refresh_inspector()

    inspector = _drop_index_if_exists(inspector, op.f("ix_app_user_referrals_registered_at"), "app_user_referrals")
    inspector = _drop_index_if_exists(inspector, op.f("ix_app_user_referrals_inviter_user_id"), "app_user_referrals")
    inspector = _drop_index_if_exists(inspector, op.f("ix_app_user_referrals_invitee_user_id"), "app_user_referrals")
    inspector = _drop_index_if_exists(inspector, op.f("ix_app_user_referrals_invite_code"), "app_user_referrals")
    inspector = _drop_index_if_exists(inspector, op.f("ix_app_user_referrals_first_paid_order_no"), "app_user_referrals")
    inspector = _drop_index_if_exists(inspector, op.f("ix_app_user_referrals_first_paid_at"), "app_user_referrals")
    if _table_exists(inspector, "app_user_referrals"):
        op.drop_table("app_user_referrals")
        inspector = _refresh_inspector()

    inspector = _drop_index_if_exists(inspector, op.f("ix_app_users_referral_code"), "app_users")
    if _column_exists(inspector, "app_users", "referral_code"):
        op.drop_column("app_users", "referral_code")
        inspector = _refresh_inspector()
    if _column_exists(inspector, "app_users", "credit_balance"):
        op.drop_column("app_users", "credit_balance")
