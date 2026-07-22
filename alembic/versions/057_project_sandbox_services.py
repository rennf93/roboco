"""Per-project sandbox DB/Redis opt-in column.

The sandboxed-DB provisioner (orchestrator-side sibling containers) only
participates for a project when ``sandbox_services`` is set — e.g.
``["postgres", "redis"]``. Additive and nullable, so existing projects keep
today's behavior (no sandbox, the legacy ``_append_gate_env`` prod-creds
injection stays byte-for-byte unchanged for them).

Revision ID: 057_project_sandbox_services
Revises: 055_spawn_session_turns
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "057_project_sandbox_services"
down_revision = "055_spawn_session_turns"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("sandbox_services", sa.ARRAY(sa.String()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "sandbox_services")
