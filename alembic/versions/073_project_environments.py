"""Per-project ordered environment ladder column.

Replaces the single ``default_branch`` as the source of truth for a project's
PR target (head rung) and release target (prod rung). The ladder is an ordered
``list[{name, branch}]``: index 0 = head (where dev/cell/leaf PRs land), index
-1 = prod (where the gated release executor commits + tags), middle rungs =
intermediates (qa/stag). The ``EnvSyncEngine`` cascades prod→…→head so dev
never falls behind prod, and the CEO-gated release promotes the full chain
head→…→prod before bumping.

Additive and nullable: an unset (null) ``environments`` falls back to a
degenerate single-branch ladder synthesized from ``default_branch`` at read
time (``roboco/services/env_branches.py``), so every existing project keeps
behaving byte-for-byte as before until the operator declares a real ladder
in the panel. ``default_branch`` is retained as the legacy/shim source.

Revision ID: 073_project_environments
Revises: 072_project_sandbox_extensions
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "073_project_environments"
down_revision = "072_project_sandbox_extensions"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("environments", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "environments")
