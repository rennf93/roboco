"""RAG index dead-letter table — durable retry queue for fire-and-forget indexes.

``_schedule_rag_index`` (journal) and ``_extract_completion_learnings`` (task)
index off the critical path: an embedder 429 after retries was swallowed +
logged, leaving the entry invisible to ``optimal.search`` / ``similar_memory``
though it lived in the DB. This table is the dead-letter — a row per dropped
index write (doc_source, payload jsonb, attempts, last_error, next_retry_at).
A startup janitor reclaims due rows with backoff; on success the row is
deleted, on failure attempts bumps and next_retry_at advances. Best-effort: a
failed index never blocks the caller's commit.

Revision ID: 065_rag_index_failures
Revises: 064_playbook_indexed_flag
Create Date: 2026-07-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "065_rag_index_failures"
down_revision = "064_playbook_indexed_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_index_failures",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("doc_source", sa.String(length=32), nullable=False),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_rag_index_failures_doc_source",
        "rag_index_failures",
        ["doc_source"],
    )
    op.create_index(
        "ix_rag_index_failures_next_retry_at",
        "rag_index_failures",
        ["next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rag_index_failures_next_retry_at", table_name="rag_index_failures"
    )
    op.drop_index("ix_rag_index_failures_doc_source", table_name="rag_index_failures")
    op.drop_table("rag_index_failures")
