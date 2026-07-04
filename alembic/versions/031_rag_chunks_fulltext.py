"""Add a full-text (tsvector) column + GIN index to every RAG chunk table.

Hybrid retrieval fuses pgvector cosine similarity with Postgres native
full-text search (keyword/BM25-style) so recall no longer depends on a
per-query HyDE LLM call. Each ``chunks_<index_type>`` table gets a generated
``tsv`` column (``to_tsvector('english', content)``, auto-maintained on
insert/update) and a GIN index over it.

The work runs inside a plpgsql ``DO`` block guarding on ``information_schema``,
so it is idempotent, offline-renderable (``alembic --sql``), and safe whether a
table is present, already has ``tsv``, or is absent (e.g. ``chunks_code``).
Depends on 030 having reshaped the column to ``content``.

Revision ID: 031_rag_chunks_fulltext
Revises: 030_rag_chunks_content_schema
Create Date: 2026-06-15
"""

from __future__ import annotations

from alembic import op

revision = "031_rag_chunks_fulltext"
down_revision = "030_rag_chunks_content_schema"
branch_labels = None
depends_on = None

CHUNK_TABLES = (
    "chunks_code",
    "chunks_documentation",
    "chunks_journals",
    "chunks_errors",
    "chunks_standards",
    "chunks_decisions",
    "chunks_reviews",
    "chunks_learnings",
)

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
                      AND table_name = t AND column_name = 'content'
                ) THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = t AND column_name = 'tsv'
                    ) THEN
                        EXECUTE format(
                            'ALTER TABLE %I ADD COLUMN tsv tsvector '
                            'GENERATED ALWAYS AS '
                            '(to_tsvector(''english'', content)) STORED',
                            t
                        );
                    END IF;
                    EXECUTE format(
                        'CREATE INDEX IF NOT EXISTS %I ON %I USING gin (tsv)',
                        t || '_tsv_idx', t
                    );
                END IF;
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
                EXECUTE format('DROP INDEX IF EXISTS %I', t || '_tsv_idx');
                EXECUTE format(
                    'ALTER TABLE IF EXISTS %I DROP COLUMN IF EXISTS tsv', t
                );
            END LOOP;
        END $$;
        """
    )
