#!/usr/bin/env bash
# Periodic pg_dump of the roboco DB (interim backup — no PITR/WAL archiving).
# Runs once on start, then every BACKUP_INTERVAL_SECONDS (default 24h). A
# failed dump logs and is retried next cycle; the loop itself never exits.
# ponytail: a plain sleep loop, not a cron daemon — good enough for a fixed
# 24h cadence, swap for a scheduler only if finer granularity is ever needed.
set -u

BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP="${BACKUP_KEEP:-14}"
INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"

# pg_dump reads these natively — no need to pass -h/-p/-U/-d by hand.
export PGHOST="${POSTGRES_HOST:-roboco-postgres}"
export PGPORT="${POSTGRES_PORT:-5432}"
export PGUSER="${POSTGRES_USER:-roboco}"
export PGPASSWORD="${POSTGRES_PASSWORD:-roboco}"
export PGDATABASE="${POSTGRES_DB:-roboco}"

mkdir -p "$BACKUP_DIR"

run_backup() {
  local ts dest
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  dest="${BACKUP_DIR}/roboco-${ts}.dump"
  echo "[backup] $(date -u -Iseconds) starting pg_dump -> ${dest}"
  if pg_dump -Fc -f "${dest}.tmp"; then
    mv "${dest}.tmp" "${dest}"
    echo "[backup] $(date -u -Iseconds) OK: ${dest}"
  else
    rm -f "${dest}.tmp"
    echo "[backup] $(date -u -Iseconds) FAILED — will retry next cycle" >&2
  fi

  # Prune to the newest $KEEP dumps. Filenames are our own fixed timestamp
  # format (no spaces/globs to worry about), so plain `ls -t` is enough.
  # shellcheck disable=SC2012
  ls -1t "${BACKUP_DIR}"/roboco-*.dump 2>/dev/null | tail -n "+$((KEEP + 1))" | while IFS= read -r old; do
    rm -f "$old"
  done
}

while true; do
  run_backup
  sleep "$INTERVAL_SECONDS"
done
