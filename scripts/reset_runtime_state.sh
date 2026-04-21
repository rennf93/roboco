#!/usr/bin/env bash
# Reset the smoke-test runtime state (tasks, sessions, messages, journal
# entries, notifications, audit, waiting, work_sessions) while preserving
# the project scaffolding (agents, projects, channels, groups, journals).
#
# Works from the host or from inside the orchestrator container — the
# script auto-detects.
#
# Usage:
#   ./scripts/reset_runtime_state.sh           # local docker
#   ssh renzof-nas.local "sudo bash -s" < scripts/reset_runtime_state.sh
#                                               # remote over ssh

set -euo pipefail

SQL_FILE="$(dirname "$0")/reset_runtime_state.sql"

if [ ! -f "$SQL_FILE" ]; then
    echo "reset_runtime_state.sql not found next to this script" >&2
    exit 1
fi

# Prefer sudo when available (NAS env); fall back to plain docker.
DOCKER="docker"
if command -v sudo >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
    DOCKER="sudo docker"
fi

if ! $DOCKER ps --format '{{.Names}}' | grep -q '^roboco-postgres$'; then
    echo "roboco-postgres container not running; start the stack first." >&2
    exit 1
fi

echo ">>> Wiping runtime DB state (preserving agents/projects/channels)..."
$DOCKER exec -i roboco-postgres psql -U roboco -d roboco < "$SQL_FILE"

# Flush Redis — it caches permission checks, session lookups, dispatcher
# heartbeats, and Redis Streams for events. Stale entries after a wipe
# mask the clean state (e.g. cached agent metrics). `FLUSHDB` drops
# everything in the default DB (which is all we use), preserving Redis
# auth/config.
if $DOCKER ps --format '{{.Names}}' | grep -q '^roboco-redis$'; then
    echo ">>> Flushing Redis cache..."
    $DOCKER exec roboco-redis redis-cli FLUSHDB | sed 's/^/    /'
fi

echo ">>> Done."
