# Database Backups

An interim `backup` sidecar service ships in both `docker-compose.yml` and `docker-compose.registry.yml`: a plain `pgvector/pgvector:pg16` container (the same image the `postgres` service pins) running `docker/scripts/backup-entrypoint.sh` on the `data` network alongside `postgres`, with no feature flag — it is always on, since a default-off backup is pointless.

## What it does

On container start, and then every `BACKUP_INTERVAL_SECONDS` (default `86400`, i.e. 24h), the script runs `pg_dump -Fc` against the `roboco` database over the network (via `PGHOST=roboco-postgres`, the same container-name resolution every other data-network consumer uses) and writes a timestamped custom-format dump to `${ROBOCO_DATA_DIR:-./data}/backups/roboco-<UTC-timestamp>.dump` on the host. The dump is written to a `.tmp` suffix first and only renamed into place on success, so a crash mid-dump never leaves a half-written file that looks complete.

## Retention

After each attempt the script prunes `${ROBOCO_DATA_DIR:-./data}/backups` down to the newest `BACKUP_KEEP` dumps (default `14`, i.e. roughly two weeks at the default 24h cadence) by mtime, deleting the rest. There is no offsite copy and no WAL/PITR archiving — this is a point-in-time `pg_dump` snapshot only, taken once a day.

## Failure behavior

A failed `pg_dump` (network hiccup, postgres briefly unhealthy, disk full) logs to `docker logs roboco-backup` and is retried on the next cycle; the loop itself never exits, so the container never crash-loops on a transient failure. `restart: unless-stopped` covers the rest.

## Restoring a dump

Stop anything writing to the database, then restore into a running (empty or throwaway) `roboco` database with `pg_restore`, for example: `docker exec -i roboco-postgres pg_restore -U roboco -d roboco --clean --if-exists < ./data/backups/roboco-20260711T030000Z.dump` (drop the `.tmp` files if any are present — they are in-progress dumps, not backups). Use `pg_restore -l <dump>` first if you want to inspect or selectively restore a subset of objects rather than the whole database. For a fresh empty database instead of `--clean`, create it first (`createdb -U roboco roboco_restore`) and restore into that.

## Known ceiling

This is an interim measure to close the "zero backups" gap, not a full disaster-recovery story: a single daily snapshot on the same host as the database it's backing up is vulnerable to whole-host loss (disk failure, NAS failure). Copying `${ROBOCO_DATA_DIR:-./data}/backups` offsite periodically, or moving to WAL-based continuous archiving, is the natural next step if that risk matters more than the current simplicity.
