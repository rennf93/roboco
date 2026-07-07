"""Add indexed_ok / indexed_at to playbooks — durable index-state tracking.

``approve`` flips a draft to APPROVED and the post-commit ``index_approved``
step embeds it into the PLAYBOOKS RAG index. The index write is best-effort
(embedder down → swallowed + logged), so an Ollama restart mid-approval-burst
left a row APPROVED but absent from the corpus — the Auditor saw "approved"
while agents never surfaced the procedure. These two columns record whether
the index write actually landed: ``indexed_ok`` is False on the status flip
and set True only inside a successful ``index_playbook`` result;
``indexed_at`` timestamps it. A startup reconcile re-indexes
APPROVED-but-``indexed_ok=False`` rows. Additive and default-off (existing
approved rows are treated as unindexed and reconciled on the next startup).

Revision ID: 064_playbook_indexed_flag
Revises: 063_video_engine_project_toggle
Create Date: 2026-07-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "064_playbook_indexed_flag"
down_revision = "063_video_engine_project_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "playbooks",
        sa.Column(
            "indexed_ok",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "playbooks",
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("playbooks", "indexed_at")
    op.drop_column("playbooks", "indexed_ok")
