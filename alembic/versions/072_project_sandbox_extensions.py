"""Per-project sandbox extensions/modules opt-in column.

The parameterized sandbox (docs/internal/specs/2026-07-13-sandbox-extensions-
on-the-fly.md) lets a venture declare the extensions/modules its sandboxed dev
DB should activate (e.g. ``{"postgres": ["vector", "postgis"], "redis":
["search"]}``). The provisioner activates them post-ready via ``docker exec``.
Additive and nullable: an unset service gets no extensions (bare), so existing
opted-in projects stay byte-for-byte unchanged on the bare path. Feature names
are allowlist-validated by the Project pydantic model before reaching here
(``SANDBOX_ENGINE_FEATURES``), so a ``plpython3u`` can never be persisted.

Revision ID: 072_project_sandbox_extensions
Revises: 071_review_findings
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "072_project_sandbox_extensions"
down_revision = "071_review_findings"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("sandbox_extensions", sa.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "sandbox_extensions")
