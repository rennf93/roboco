"""Add the tiktok_credentials table — the TikTok inbox-upload poster.

Singleton row (mirrors ``x_credentials``, migration 059) holding the
Fernet-encrypted OAuth2 secrets: client_key/client_secret (static) plus
access_token/refresh_token (rotate on refresh — ``TikTokCredentialsService.
update_tokens`` is the narrower write for that path). Additive and inert
until real TikTok credentials are set.

Revision ID: 062_tiktok_credentials
Revises: 061_x_feature_spotlight
Create Date: 2026-07-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "062_tiktok_credentials"
down_revision = "061_x_feature_spotlight"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiktok_credentials",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("client_key_encrypted", sa.Text(), nullable=True),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("tiktok_credentials")
