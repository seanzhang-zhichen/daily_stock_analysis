"""seed free plan

Revision ID: 20260528_seed_free_plan
Revises: 20260527_widen_eval_status
Create Date: 2026-05-28 11:45:00.000000+08:00
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260528_seed_free_plan"
down_revision: Union[str, None] = "20260527_widen_eval_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


app_plans = sa.table(
    "app_plans",
    sa.column("code", sa.String(length=32)),
    sa.column("name", sa.String(length=64)),
    sa.column("daily_analysis_limit", sa.Integer()),
    sa.column("daily_agent_limit", sa.Integer()),
    sa.column("max_stocks", sa.Integer()),
    sa.column("allowed_models", sa.Text()),
    sa.column("can_webhook", sa.Boolean()),
    sa.column("price_cents", sa.Integer()),
    sa.column("currency", sa.String(length=8)),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime()),
    sa.column("updated_at", sa.DateTime()),
)


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT 1 FROM app_plans WHERE code = :code"),
        {"code": "free"},
    ).first()
    if exists is not None:
        return

    now = datetime.utcnow()
    bind.execute(
        app_plans.insert().values(
            code="free",
            name="免费会员",
            daily_analysis_limit=5,
            daily_agent_limit=5,
            max_stocks=3,
            allowed_models=None,
            can_webhook=False,
            price_cents=0,
            currency="CNY",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )


def downgrade() -> None:
    return
