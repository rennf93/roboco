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
#
# The tag names the Debian release (`pg16` is the same image today) so the
# release the apt pin below is relative to cannot move under it.

FROM pgvector/pgvector:pg16-bookworm

# PGDG keeps only the newest PostGIS 3 minor for PG 16 in its index (older
# ones move to apt-archive.postgresql.org), so the series it never removes is
# the major the package name already carries: a minor pin would fail the build
# on PGDG's next PostGIS release.
RUN apt-get update \
    && apt-get install -y --no-install-recommends 'postgresql-16-postgis-3=3.*' \
    && rm -rf /var/lib/apt/lists/*

# Runs as the image's own postgres user (uid/gid 999, fixed by the official
# image), so docker-entrypoint.sh skips its root-only chown + gosu step and
# the provisioner's `docker exec` probes run unprivileged too. The provisioner
# mounts a root-owned 1777 tmpfs at /var/lib/postgresql/data, which postgres
# can write into but cannot chmod; initdb needs a data dir it owns at 0700, so
# the cluster lives one level down, in a directory postgres creates itself.
ENV PGDATA=/var/lib/postgresql/data/pgdata
USER 999:999

# Over TCP, not the socket: during first init the entrypoint runs a
# socket-only temporary server, which must not read as ready. User and
# database are the container's own (the entrypoint's defaults), so the probe
# never logs a FATAL; sh expands them at probe time.
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=5 \
    CMD ["sh", "-c", "pg_isready -q -h 127.0.0.1 -U \"${POSTGRES_USER:-postgres}\" -d \"${POSTGRES_DB:-${POSTGRES_USER:-postgres}}\""]

LABEL org.opencontainers.image.title="RoboCo sandbox postgres (kitchen-sink)"
LABEL org.opencontainers.image.description="PostgreSQL 16 + vector + postgis + contrib for parameterized sandbox dev DBs"
