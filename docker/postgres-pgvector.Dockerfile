# =============================================================================
# PostgreSQL with pgvector on Docker Hardened Image
# =============================================================================
# Multi-stage build:
#   1. Build pgvector extension using standard postgres image (has build tools)
#   2. Copy compiled extension to DHI postgres (minimal, secure runtime)
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Build pgvector extension
# -----------------------------------------------------------------------------
FROM postgres:17 AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    postgresql-server-dev-17 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Clone and build pgvector (use tagged release for reproducibility)
ARG PGVECTOR_VERSION=0.8.1
RUN git clone --branch v${PGVECTOR_VERSION} --depth 1 https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && make OPTFLAGS="" \
    && make install

# -----------------------------------------------------------------------------
# Stage 2: DHI Runtime with pgvector
# -----------------------------------------------------------------------------
FROM dhi.io/postgres:17-debian13

# Copy pgvector extension files from builder
# Extension shared library
COPY --from=builder /usr/lib/postgresql/17/lib/vector.so /usr/lib/postgresql/17/lib/
# Extension control and SQL files
COPY --from=builder /usr/share/postgresql/17/extension/vector* /usr/share/postgresql/17/extension/

# Add init script to create extension on startup
COPY docker/postgres-init/01-create-extensions.sql /docker-entrypoint-initdb.d/

LABEL org.opencontainers.image.title="PostgreSQL with pgvector (DHI)"
LABEL org.opencontainers.image.description="Docker Hardened PostgreSQL 17 with pgvector extension for vector similarity search"
