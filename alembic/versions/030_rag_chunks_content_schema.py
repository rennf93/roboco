"""Align RAG chunk tables with the in-house vector-store schema.

The in-house vector store (which replaced piragi) reads and writes a ``content``
column and a ``created_at`` column on every ``chunks_<index_type>`` table, and
provisions those tables at runtime with ``CREATE TABLE IF NOT EXISTS``. On a
database that already carried the piragi-era tables — column named ``text``, no
``created_at`` — that runtime DDL is a silent no-op, so the new engine never
reshapes them and every ingest/search/list fails with
``column "content" of relation "chunks_<type>" does not exist``.

These tables hold derived chunks AND non-rebuildable agent knowledge
(journals, decisions, errors, learnings, reviews, conversations) that a docs
reindex cannot regenerate, so this migration ALTERs in place — renaming
``text`` -> ``content`` and adding ``created_at`` — rather than dropping data.
The legacy ``chunk_index`` / integer ``id`` columns are left untouched: the
engine never references them and dropping them would be a needless destructive
change.

Both directions guard on actual column presence, so the migration is idempotent
and safe whether a table is piragi-shaped, already engine-shaped, or absent
(e.g. ``chunks_code``, which has never been populated and will be created fresh
in the correct shape by the engine).

Revision ID: 030_rag_chunks_content_schema
Revises: 029_project_quality_command
Create Date: 2026-06-15
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from sqlalchemy.engine import Inspector

revision = "030_rag_chunks_content_schema"
down_revision = "029_project_quality_command"
branch_labels = None
depends_on = None

# Frozen snapshot of the chunk tables — one per ``IndexType`` value at the time
# of writing. Hardcoded so the migration stays self-contained and never imports
# evolving application code; a unit test guards this tuple against the live
# ``IndexType`` enum so a newly added index type cannot silently escape the
# schema alignment.
CHUNK_TABLES = (
    "chunks_code",
    "chunks_documentation",
    "chunks_conversations",
    "chunks_journals",
    "chunks_errors",
    "chunks_standards",
    "chunks_decisions",
    "chunks_reviews",
    "chunks_learnings",
)


def _columns(inspector: Inspector, table: str) -> set[str]:
    """Return the set of column names currently present on ``table``."""
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in CHUNK_TABLES:
        if not inspector.has_table(table):
            continue
        cols = _columns(inspector, table)
        if "text" in cols and "content" not in cols:
            op.alter_column(table, "text", new_column_name="content")
        if "created_at" not in cols:
            op.add_column(
                table,
                sa.Column(
                    "created_at",
                    sa.TIMESTAMP(timezone=True),
                    nullable=False,
                    server_default=sa.text("NOW()"),
                ),
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in CHUNK_TABLES:
        if not inspector.has_table(table):
            continue
        cols = _columns(inspector, table)
        if "created_at" in cols:
            op.drop_column(table, "created_at")
        if "content" in cols and "text" not in cols:
            op.alter_column(table, "content", new_column_name="text")
