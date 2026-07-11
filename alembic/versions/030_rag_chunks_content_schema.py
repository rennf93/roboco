"""Align RAG chunk tables with the in-house vector-store schema.

The in-house vector store (which replaced piragi) reads and writes a ``content``
column and a ``created_at`` column on every ``chunks_<index_type>`` table, and
provisions those tables at runtime with ``CREATE TABLE IF NOT EXISTS``. On a
database that already carried the piragi-era tables — column named ``text``, no
``created_at`` — that runtime DDL is a silent no-op, so the new engine never
reshapes them and every ingest/search/list fails with
``column "content" of relation "chunks_<type>" does not exist``.

These tables hold derived chunks AND non-rebuildable agent knowledge
(journals, decisions, errors, learnings, reviews) that a docs
reindex cannot regenerate, so this migration ALTERs in place — renaming
``text`` -> ``content`` and adding ``created_at`` — rather than dropping data.
The legacy ``chunk_index`` / integer ``id`` columns are left untouched: the
engine never references them and dropping them would be a needless destructive
change.

The work runs inside a single plpgsql ``DO`` block that guards each step on
``information_schema`` at execution time. That keeps the migration idempotent
and safe whether a table is piragi-shaped, already engine-shaped, or absent
(e.g. ``chunks_code``, never populated) — and, unlike Python-side reflection,
it renders under ``alembic upgrade --sql`` (offline mode) where no live
connection exists.

Revision ID: 030_rag_chunks_content_schema
Revises: 029_project_quality_command
Create Date: 2026-06-15
"""

from __future__ import annotations

from alembic import op

revision = "030_rag_chunks_content_schema"
down_revision = "029_project_quality_command"
branch_labels = None
depends_on = None

# One per ``IndexType`` value at the time of writing. Hardcoded so the migration
# stays self-contained and never imports evolving application code; a unit test
# guards this tuple against the live ``IndexType`` enum so a newly added index
# type cannot silently escape the schema alignment.
CHUNK_TABLES = (
    "chunks_code",
    "chunks_documentation",
    "chunks_journals",
    "chunks_errors",
    "chunks_standards",
    "chunks_decisions",
    "chunks_reviews",
    "chunks_learnings",
    "chunks_playbooks",
    "chunks_vault_notes",
)

# SQL array literal of the table names (validated identifiers from the tuple).
_TABLES_SQL = ", ".join(f"'{name}'" for name in CHUNK_TABLES)


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            t text;
        BEGIN
            FOREACH t IN ARRAY ARRAY[{_TABLES_SQL}] LOOP
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = t AND column_name = 'text'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = t AND column_name = 'content'
                ) THEN
                    EXECUTE format('ALTER TABLE %I RENAME COLUMN text TO content', t);
                END IF;
                EXECUTE format(
                    'ALTER TABLE IF EXISTS %I '
                    'ADD COLUMN IF NOT EXISTS created_at '
                    'TIMESTAMPTZ NOT NULL DEFAULT NOW()',
                    t
                );
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            t text;
        BEGIN
            FOREACH t IN ARRAY ARRAY[{_TABLES_SQL}] LOOP
                EXECUTE format(
                    'ALTER TABLE IF EXISTS %I DROP COLUMN IF EXISTS created_at', t
                );
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = t AND column_name = 'content'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = t AND column_name = 'text'
                ) THEN
                    EXECUTE format('ALTER TABLE %I RENAME COLUMN content TO text', t);
                END IF;
            END LOOP;
        END $$;
        """
    )
