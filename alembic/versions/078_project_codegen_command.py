"""Per-project codegen-drift regeneration command.

Some projects check in generated artifacts (rendered docs, generated verb/prompt
tables, ...) that drift whenever their source changes. The agent pre-submit
gate (``quality_command``, e.g. ``make gate``) never regenerates them, so drift
only ever surfaces later as CI's own foundation-check-style `git diff
--exit-code` hard-fail — a failure an agent has no way to trace back to its own
task. ``codegen_command`` (e.g. ``make codegen``) is run in the task's
workspace before every push; any resulting drift is committed so the pushed PR
head is never stale. Additive and nullable — null (the default) is a pure
no-op for projects with no generated artifacts, mirroring ``quality_command``.

Revision ID: 078_project_codegen_command
Revises: 077_github_app
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "078_project_codegen_command"
down_revision = "077_github_app"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("codegen_command", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "codegen_command")
