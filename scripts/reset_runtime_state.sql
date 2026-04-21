-- Reset runtime state between smoke-test runs without dropping the
-- organisational scaffolding. Preserves:
--   - agents, projects, channels (parent rows)
--   - alembic_version (schema version)
--
-- Clears:
--   - tasks and everything that references them (sessions, messages,
--     notifications, journal_entries, journals, groups, audit_log,
--     waiting_records, work_sessions, session_tasks, handoffs, a2a_*)
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
UNION ALL SELECT 'sessions', COUNT(*) FROM sessions
UNION ALL SELECT 'messages', COUNT(*) FROM messages
UNION ALL SELECT 'notifications', COUNT(*) FROM notifications
UNION ALL SELECT 'journal_entries', COUNT(*) FROM journal_entries
UNION ALL SELECT 'audit_log', COUNT(*) FROM audit_log
UNION ALL SELECT 'waiting_records', COUNT(*) FROM waiting_records
UNION ALL SELECT 'work_sessions', COUNT(*) FROM work_sessions
UNION ALL SELECT 'session_tasks', COUNT(*) FROM session_tasks
UNION ALL SELECT 'groups', COUNT(*) FROM groups
UNION ALL SELECT 'journals', COUNT(*) FROM journals;

-- Clear in FK-safe order. Each statement uses IF EXISTS on the table
-- name via a DO block so the script keeps running if a table is missing
-- (e.g. a2a_* tables only exist when the A2A migration has run).
DO $$
DECLARE
    tbl text;
    -- Ordered most-dependent first so FK constraints are satisfied without
    -- `CASCADE`. `groups` and `journals` are included because both are
    -- created per-run (groups per task-initiative, journals per agent
    -- sitting); they're not the persistent channel/agent scaffolding.
    -- Clearing them removes stale per-run rows that accumulate between
    -- smoke tests.
    drop_order text[] := ARRAY[
        'a2a_messages',
        'a2a_conversations',
        'handoffs',
        'messages',
        'journal_entries',
        'journals',
        'notifications',
        'audit_log',
        'waiting_records',
        'session_tasks',
        'work_sessions',
        'sessions',
        'groups',
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
        'chunks_conversations',
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

-- Reset groups' active_session_id since the sessions it points at are gone.
-- The column is nullable; null means "no active session yet". The
-- IF EXISTS guard keeps the script happy if the column was renamed or
-- removed in a future schema change.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'groups'
          AND column_name = 'active_session_id'
    ) THEN
        UPDATE groups SET active_session_id = NULL;
    END IF;
END $$;

\echo ==== AFTER ====
SELECT
    'tasks' AS table, COUNT(*) FROM tasks
UNION ALL SELECT 'sessions', COUNT(*) FROM sessions
UNION ALL SELECT 'messages', COUNT(*) FROM messages
UNION ALL SELECT 'notifications', COUNT(*) FROM notifications
UNION ALL SELECT 'journal_entries', COUNT(*) FROM journal_entries
UNION ALL SELECT 'audit_log', COUNT(*) FROM audit_log
UNION ALL SELECT 'waiting_records', COUNT(*) FROM waiting_records
UNION ALL SELECT 'work_sessions', COUNT(*) FROM work_sessions
UNION ALL SELECT 'session_tasks', COUNT(*) FROM session_tasks
UNION ALL SELECT 'groups', COUNT(*) FROM groups
UNION ALL SELECT 'journals', COUNT(*) FROM journals;

\echo ==== PRESERVED ====
SELECT
    'agents' AS table, COUNT(*) FROM agents
UNION ALL SELECT 'projects', COUNT(*) FROM projects
UNION ALL SELECT 'channels', COUNT(*) FROM channels;

COMMIT;
