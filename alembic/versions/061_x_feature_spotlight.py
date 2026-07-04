"""Add x_seen_features table + company_goals.brand_voice — feature-spotlight.

``x_seen_features`` is the feature-spotlight dedup ledger, keyed by feature
slug so a shipped capability is never spotlighted twice (mirrors
``x_seen_mentions``). ``company_goals.brand_voice`` is the CEO-authored
brand-voice sample/direction, a dedicated Text column mirroring
``north_star`` (not folded into the catch-all ``operating_policy`` JSON
blob). Additive and inert while ``ROBOCO_X_FEATURE_SPOTLIGHT_ENABLED`` is off.

Revision ID: 061_x_feature_spotlight
Revises: 060_drop_messaging
Create Date: 2026-07-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "061_x_feature_spotlight"
down_revision = "060_drop_messaging"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.create_table(
        "x_seen_features",
        sa.Column("feature_slug", sa.String(length=128), primary_key=True),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "company_goals",
        sa.Column("brand_voice", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("company_goals", "brand_voice")
    op.drop_table("x_seen_features")
