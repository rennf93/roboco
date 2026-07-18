"""Add projects.git_provider — Phase 0 of the forge-providers spec.

Nullable ``git_provider`` (plain string, not a pg enum — validated at the
service layer by ``roboco.foundation.policy.forge.validate_project_forge``
instead of a DB constraint, mirroring how ``assigned_cell``-adjacent free-text
columns like ``ci_watch_workflow`` are validated in Python, not SQL). Null
means "auto-detect from git_url host" (github.com -> github; anything else is
a registration-time rejection unless the operator sets this column explicitly
— the GitHub Enterprise escape hatch). Additive and inert: every existing
project keeps resolving to GitHub behavior until GitLab/Gitea providers land
in a later phase.

Revision ID: 075_project_git_provider
Revises: 074_telegram_credentials
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "075_project_git_provider"
down_revision = "074_telegram_credentials"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("git_provider", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "git_provider")
