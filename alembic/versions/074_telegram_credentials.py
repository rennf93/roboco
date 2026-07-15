"""Add the telegram_credentials table — the Telegram notifications bridge.

Singleton row (mirrors ``x_credentials``, migration 059) holding the
Fernet-encrypted bot token (from @BotFather) + chat id (the CEO's
destination). Additive and inert until real Telegram credentials are set and
``telegram_enabled`` is armed.

Revision ID: 074_telegram_credentials
Revises: 073_project_environments
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "074_telegram_credentials"
down_revision = "073_project_environments"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_credentials",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("bot_token_encrypted", sa.Text(), nullable=True),
        sa.Column("chat_id_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("telegram_credentials")
