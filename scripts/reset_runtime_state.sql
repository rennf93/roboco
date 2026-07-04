-- Reset runtime state between smoke-test runs without dropping the
-- organisational scaffolding. Preserves:
--   - agents, projects (parent rows)
--   - alembic_version (schema version)
--
-- Clears:
--   - tasks and everything that references them (notifications,
--     journal_entries, journals, audit_log, waiting_records, work_sessions,
--     handoffs, a2a_*)
--
-- Usage:
--   docker exec -i roboco-postgres psql -U roboco -d roboco \
--       -f - < scripts/reset_runtime_state.sql
-- or piped:
--   cat scripts/reset_runtime_state.sql | \
--       docker exec -i roboco-postgres psql -U roboco -d roboco
--
-- Safe to re-run — every clear is idempotent (DELETE, not DROP), and the
-- preserved tables never get touched.

BEGIN;

-- Helper: show row counts before + after so the caller sees what happened.
\echo ==== BEFORE ====
SELECT
    'tasks' AS table, COUNT(*) FROM tasks
UNION ALL SELECT 'notifications', COUNT(*) FROM notifications
UNION ALL SELECT 'journal_entries', COUNT(*) FROM journal_entries
UNION ALL SELECT 'audit_log', COUNT(*) FROM audit_log
UNION ALL SELECT 'waiting_records', COUNT(*) FROM waiting_records
UNION ALL SELECT 'work_sessions', COUNT(*) FROM work_sessions
UNION ALL SELECT 'journals', COUNT(*) FROM journals;

-- Clear in FK-safe order. Each statement uses IF EXISTS on the table
-- name via a DO block so the script keeps running if a table is missing
-- (e.g. a2a_* tables only exist when the A2A migration has run).
DO $$
DECLARE
    tbl text;
    -- Ordered most-dependent first so FK constraints are satisfied without
    -- `CASCADE`. `journals` is included because it's created per-agent
    -- sitting, not persistent scaffolding; clearing it removes stale
    -- per-run rows that accumulate between smoke tests.
    drop_order text[] := ARRAY[
        'a2a_messages',
        'a2a_conversations',
        'handoffs',
        'journal_entries',
        'journals',
        'notifications',
        'audit_log',
        'waiting_records',
        'work_sessions',
        'tasks'
    ];
BEGIN
    FOREACH tbl IN ARRAY drop_order LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = tbl
        ) THEN
            EXECUTE format('DELETE FROM %I', tbl);
        END IF;
    END LOOP;
END $$;

-- Clear RAG (piragi) indexes for runtime-generated content. Keep the
-- static-doc indexes (`chunks_documentation`, `chunks_standards`) because
-- those are part of the project's curated knowledge base, not smoke-test
-- residue. Also purge `indexed_documents` rows that reference cleared
-- chunk types, so the admin panel counters reflect the real state.
DO $$
DECLARE
    tbl text;
    rag_drop text[] := ARRAY[
        'chunks_decisions',
        'chunks_errors',
        'chunks_journals',
        'chunks_learnings',
        'chunks_reviews'
    ];
BEGIN
    FOREACH tbl IN ARRAY rag_drop LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = tbl
        ) THEN
            EXECUTE format('DELETE FROM %I', tbl);
        END IF;
    END LOOP;
    -- indexed_documents tracks what's been ingested into each chunk index.
    -- Drop the rows that point at the cleared indexes; keep docs for the
    -- preserved indexes (documentation, standards).
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'indexed_documents'
    ) THEN
        DELETE FROM indexed_documents
        WHERE index_type = ANY(ARRAY[
            'conversations', 'decisions', 'errors',
            'journals', 'learnings', 'reviews'
        ]);
    END IF;
END $$;

-- Reset per-agent aggregate counters on agents.metrics so the UI's
-- journal summary doesn't show stale "8 entries" after the rows are gone.
-- metrics is a generic JSON blob so we just wipe it back to {}.
UPDATE agents SET metrics = '{}'::json WHERE metrics::text <> '{}';

\echo ==== AFTER ====
SELECT
    'tasks' AS table, COUNT(*) FROM tasks
UNION ALL SELECT 'notifications', COUNT(*) FROM notifications
UNION ALL SELECT 'journal_entries', COUNT(*) FROM journal_entries
UNION ALL SELECT 'audit_log', COUNT(*) FROM audit_log
UNION ALL SELECT 'waiting_records', COUNT(*) FROM waiting_records
UNION ALL SELECT 'work_sessions', COUNT(*) FROM work_sessions
UNION ALL SELECT 'journals', COUNT(*) FROM journals;

\echo ==== PRESERVED ====
SELECT
    'agents' AS table, COUNT(*) FROM agents
UNION ALL SELECT 'projects', COUNT(*) FROM projects;

COMMIT;
