"""Add the x_credentials + x_seen_mentions tables — the X (Twitter) engine.

``x_credentials`` is a singleton row holding the Fernet-encrypted OAuth 1.0a
user-context secrets (mirrors provider_configs' encrypted-token column).
``x_seen_mentions`` is the mentions-poll dedup ledger, keyed by mention id so
a mention is never turned into a second held reply proposal. Additive and
inert while ``ROBOCO_X_ENGINE_ENABLED`` is off.

Revision ID: 059_x_credentials
Revises: 058_cloud_auth_users
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "059_x_credentials"
down_revision = "058_cloud_auth_users"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.create_table(
        "x_credentials",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("access_token_secret_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "x_seen_mentions",
        sa.Column("mention_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("x_seen_mentions")
    op.drop_table("x_credentials")
