-- =============================================================================
-- PostgreSQL Extensions Initialization
-- This script runs automatically on first database initialization
-- =============================================================================

-- pgvector: Vector similarity search for embeddings/RAG
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify extension is available
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        RAISE EXCEPTION 'pgvector extension failed to install';
    END IF;
    RAISE NOTICE 'pgvector extension installed successfully';
END $$;
