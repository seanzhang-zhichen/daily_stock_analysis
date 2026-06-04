"""add credit purchase tables

Revision ID: 20260603_credit_purchases
Revises: 20260603_credit_referrals
Create Date: 2026-06-03 15:30:00.000000+08:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260603_credit_purchases"
down_revision: Union[str, None] = "20260603_credit_referrals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


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

    if not _table_exists(inspector, "app_credit_packages"):
        op.create_table(
            "app_credit_packages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("credit_amount", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = _refresh_inspector()
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_credit_packages_code"),
        "app_credit_packages",
        ["code"],
        unique=True,
    )
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_credit_packages_is_active"),
        "app_credit_packages",
        ["is_active"],
    )

    if not _table_exists(inspector, "app_credit_orders"):
        op.create_table(
            "app_credit_orders",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("order_no", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("package_code", sa.String(length=32), nullable=False),
            sa.Column("credit_amount", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("original_amount_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("discount_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("coupon_code", sa.String(length=64), nullable=True),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
            sa.Column("provider", sa.String(length=16), nullable=False, server_default="manual"),
            sa.Column("provider_trade_no", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="created"),
            sa.Column("client_ip", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("quote_snapshot", sa.Text(), nullable=True),
            sa.Column("paid_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = _refresh_inspector()
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_credit_orders_order_no"),
        "app_credit_orders",
        ["order_no"],
        unique=True,
    )
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_credit_orders_package_code"),
        "app_credit_orders",
        ["package_code"],
    )
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_credit_orders_status"),
        "app_credit_orders",
        ["status"],
    )
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_credit_orders_user_id"),
        "app_credit_orders",
        ["user_id"],
    )
    inspector = _create_index_if_missing(
        inspector,
        "ix_app_credit_orders_provider_status",
        "app_credit_orders",
        ["provider", "status"],
    )
    inspector = _create_index_if_missing(
        inspector,
        "ix_app_credit_orders_user_created",
        "app_credit_orders",
        ["user_id", "created_at"],
    )
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_credit_orders_provider_trade_no"),
        "app_credit_orders",
        ["provider_trade_no"],
        unique=True,
    )

    if not _table_exists(inspector, "app_credit_payment_events"):
        op.create_table(
            "app_credit_payment_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("order_no", sa.String(length=32), nullable=False),
            sa.Column("provider", sa.String(length=16), nullable=False),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("provider_event_id", sa.String(length=128), nullable=False),
            sa.Column("raw_payload", sa.Text(), nullable=True),
            sa.Column("signature", sa.String(length=512), nullable=True),
            sa.Column("signature_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.Column("received_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider_event_id"),
        )
        inspector = _refresh_inspector()
    inspector = _create_index_if_missing(
        inspector,
        op.f("ix_app_credit_payment_events_order_no"),
        "app_credit_payment_events",
        ["order_no"],
    )
    _create_index_if_missing(
        inspector,
        op.f("ix_app_credit_payment_events_provider"),
        "app_credit_payment_events",
        ["provider"],
    )


def downgrade() -> None:
    inspector = _refresh_inspector()

    inspector = _drop_index_if_exists(
        inspector,
        op.f("ix_app_credit_payment_events_provider"),
        "app_credit_payment_events",
    )
    inspector = _drop_index_if_exists(
        inspector,
        op.f("ix_app_credit_payment_events_order_no"),
        "app_credit_payment_events",
    )
    if _table_exists(inspector, "app_credit_payment_events"):
        op.drop_table("app_credit_payment_events")
        inspector = _refresh_inspector()

    for name in [
        op.f("ix_app_credit_orders_provider_trade_no"),
        "ix_app_credit_orders_user_created",
        "ix_app_credit_orders_provider_status",
        op.f("ix_app_credit_orders_user_id"),
        op.f("ix_app_credit_orders_status"),
        op.f("ix_app_credit_orders_package_code"),
        op.f("ix_app_credit_orders_order_no"),
    ]:
        inspector = _drop_index_if_exists(inspector, name, "app_credit_orders")
    if _table_exists(inspector, "app_credit_orders"):
        op.drop_table("app_credit_orders")
        inspector = _refresh_inspector()

    inspector = _drop_index_if_exists(
        inspector,
        op.f("ix_app_credit_packages_is_active"),
        "app_credit_packages",
    )
    inspector = _drop_index_if_exists(
        inspector,
        op.f("ix_app_credit_packages_code"),
        "app_credit_packages",
    )
    if _table_exists(inspector, "app_credit_packages"):
        op.drop_table("app_credit_packages")
