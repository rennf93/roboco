"""Add the users table — the single seeded CEO login for cloud auth.

Cloud auth (default off, ROBOCO_CLOUD_AUTH_ENABLED) needs a FastAPI Users
schema to back the cookie-session login. Additive and unused while the flag
is off; the one row is idempotently upserted at startup, never via a
registration route.

Revision ID: 058_cloud_auth_users
Revises: 057_project_sandbox_services
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "058_cloud_auth_users"
down_revision = "057_project_sandbox_services"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "is_verified", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
