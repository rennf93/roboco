# Sandbox kitchen-sink postgres: every allowlisted pg extension present so any
# requested subset can be activated post-ready by the provisioner's
# `CREATE EXTENSION IF NOT EXISTS` enable step. Built at deploy time like the
# agent images (docker-compose `sandbox-pg-image` one-shot); the provisioner's
# `_ensure_image` finds the local tag via `image inspect` and never pulls.
#
# Base is the Debian-flavored pgvector image (ships `vector`), which inherits
# `postgresql-contrib` from the official postgres image — so pg_trgm / citext /
# uuid-ossp control files + libs are already present. PostGIS is the one
# extension not in contrib, so it is the only install. The provisioner's verify
# step (pg_extension count) fails loudly if an extension's files are missing,
# so a bad build surfaces at first provision, not as a silent query error.
# See docs/internal/specs/2026-07-13-sandbox-extensions-on-the-fly.md.

FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
    && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.title="RoboCo sandbox postgres (kitchen-sink)"
LABEL org.opencontainers.image.description="PostgreSQL 16 + vector + postgis + contrib for parameterized sandbox dev DBs"