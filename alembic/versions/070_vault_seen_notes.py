"""Add vault_seen_notes table — the vault-intake watcher's dedup ledger.

Keyed by (vault-relative path, content hash) together: an unchanged note
never reprocesses, but an edited note (new hash) is eligible again. Mirrors
``x_seen_mentions``. Additive and inert while ``ROBOCO_VAULT_INTAKE_ENABLED``
is off.

Revision ID: 070_vault_seen_notes
Revises: 069_tasks_parent_task_id_idx
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "070_vault_seen_notes"
down_revision = "069_tasks_parent_task_id_idx"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.create_table(
        "vault_seen_notes",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("note_path", sa.String(length=512), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "note_path", "content_hash", name="uq_vault_seen_notes_path_hash"
        ),
    )


def downgrade() -> None:
    op.drop_table("vault_seen_notes")
