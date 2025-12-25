# =============================================================================
# PostgreSQL with pgvector (Custom Build Example)
# =============================================================================
# Example: Build pgvector from source on standard postgres image
# Currently unused - docker-compose.yml uses pgvector/pgvector:pg16 directly
# =============================================================================

FROM pgvector/pgvector:pg17

# Add init script to create extension on startup
COPY docker/postgres-init/01-create-extensions.sql /docker-entrypoint-initdb.d/

LABEL org.opencontainers.image.title="PostgreSQL with pgvector"
LABEL org.opencontainers.image.description="PostgreSQL 17 with pgvector extension for vector similarity search"
