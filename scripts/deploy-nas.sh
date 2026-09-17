#!/usr/bin/env bash
# RoboCo NAS deploy - blue-green in ONE compose file (design:
# docs/internal/nas-blue-green-deploy.md).
#
# Run ON the NAS, as root:
#   sudo bash /volume1/roboco/scripts/deploy-nas.sh --color green
#   sudo bash /volume1/roboco/scripts/deploy-nas.sh --color blue --skip-build
#
# ONE compose file (docker-compose.yaml), ONE project (roboco). Blue services
# carry no profile (plain `up` = core + blue + nginx, the steady state);
# green services carry profiles: [green]. The ACTIVE color lives only in
# front/active-upstreams.conf, applied to the running nginx with
# `nginx -s reload`. The stopped color = the rollback (re-run with its
# color). Never `down -v`: the minio named volume is the only thing a
# volumes prune would delete.
#
# Two NAS-compose landmines baked into this script:
# 1. `up --wait` aborts when a one-shot container completes, even exit 0
#    (minio-init killed a run mid-deploy while everything was healthy) ->
#    `up -d` + poll docker health per container instead.
# 2. If nginx ever started before front/active-upstreams.conf existed,
#    docker bind-mounts a DIRECTORY at that path; the script detects and
#    removes the squatter, writes the real file, and force-recreates nginx
#    (a running container stays pinned to the old directory inode - a host
#    file write alone never reaches it).

set -euo pipefail

cd "${STACK_DIR:-/volume1/roboco}"

COLOR=blue
SKIP_BUILD=0
POLL_TIMEOUT=${POLL_TIMEOUT:-900}
INCLUDE=front/active-upstreams.conf

while [ $# -gt 0 ]; do
  case "$1" in
    --color) COLOR="$2"; shift 2 ;;
    --color=*) COLOR="${1#--color=}"; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    *) echo "unknown argument: $1 (usage: deploy-nas.sh [--color blue|green] [--skip-build])" >&2; exit 2 ;;
  esac
done

case "$COLOR" in
  blue|green) ;;
  *) echo "--color must be blue or green (got: $COLOR)" >&2; exit 2 ;;
esac

OTHER=blue
[ "$COLOR" = "blue" ] && OTHER=green

wait_healthy() { # wait_healthy <container> [exit0]
  local c="$1" must_exit="${2:-}" deadline=$((SECONDS + POLL_TIMEOUT)) st
  while [ "$SECONDS" -lt "$deadline" ]; do
    st=$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c" 2>/dev/null || echo missing)
    case "$st" in
      running/healthy) echo "[deploy] $c healthy"; return 0 ;;
      exited/none)
        if [ "$must_exit" = "exit0" ] &&
           [ "$(docker inspect -f '{{.State.ExitCode}}' "$c")" = "0" ]; then
          echo "[deploy] $c completed (exit 0)"; return 0
        fi
        ;;
    esac
    sleep 5
  done
  echo "[deploy] TIMEOUT waiting for $c (last state: $st)" >&2
  docker logs "$c" --tail 30 2>&1 | tail -10 >&2 || true
  return 1
}

COMPOSE=(docker compose -f docker-compose.yaml)

echo "[deploy] app/$COLOR: building images..."
if [ "$SKIP_BUILD" -eq 0 ]; then
  "${COMPOSE[@]}" build "orchestrator-$COLOR" "panel-$COLOR"
fi

echo "[deploy] bringing up $COLOR ..."
# Named-service up: starts ONLY this generation (+ its dependencies: core,
# agent builders). Blue (no-profile) is in every model, so a plain
# `--profile green up` would reconcile blue too - recreating it with new
# code and destroying rollback honesty. nginx joins the blue list (first
# deploy) and is ensured for green (it is the reload target).
if [ "$COLOR" = "green" ]; then
  "${COMPOSE[@]}" up -d --build orchestrator-green dispatcher-green indexer-green panel-green
  "${COMPOSE[@]}" up -d nginx
else
  "${COMPOSE[@]}" up -d --build orchestrator-blue dispatcher-blue indexer-blue panel-blue nginx
fi
wait_healthy "roboco-orchestrator-$COLOR"
wait_healthy roboco-postgres
wait_healthy roboco-ollama
wait_healthy roboco-ollama-init exit0

echo "[deploy] ensuring ALL needed images exist (blue/green set + agents)..."
# Spawns and first-ups must never discover a missing image at runtime: the
# orchestrator/dispatcher containers have no source tree, so a build they
# trigger fails with "unable to prepare context: path /volume1/roboco not
# found" (2026-09-17, secretary/prompter live chats after the rebuild wiped
# the old agent images). Everything is ensured HERE, on the host, where the
# context exists. Idempotent: present images are skipped by inspect.
echo "[deploy] - compose-declared images (core + $COLOR)..."
while IFS= read -r img; do
  [ -z "$img" ] && continue
  # The rollback color's app images are rebuilt by its own re-run; don't
  # warn about them here (a green deploy legitimately leaves them absent).
  case "$img" in *"-$OTHER") continue ;; esac
  if docker image inspect "$img" >/dev/null 2>&1; then
    echo "[deploy] $img present, skipping"
  elif docker pull -q "$img" >/dev/null 2>&1; then
    echo "[deploy] $img pulled"
  else
    echo "[deploy] WARNING: $img neither present nor pullable (built service? should have been built above)" >&2
  fi
done < <("${COMPOSE[@]}" config --images)

echo "[deploy] - agent images (build any missing ON THE HOST)..."
for df in docker/agent-*.Dockerfile; do
  img="roboco-agent-$(basename "$df" .Dockerfile | sed 's/^agent-//')"
  if docker image inspect "$img:latest" >/dev/null 2>&1; then
    echo "[deploy] $img present, skipping"
  else
    echo "[deploy] building $img ..."
    docker build -q -t "$img:latest" -f "$df" .
  fi
done

echo "[deploy] schema migrations (idempotent)..."
"${COMPOSE[@]}" exec -T "orchestrator-$COLOR" alembic upgrade head ||
  echo "[deploy] WARNING: alembic failed; traffic NOT flipped, investigate before flipping"

echo "[deploy] switching traffic to $COLOR..."
mkdir -p front
# A directory squatting on the include path (from an nginx start that
# preceded the file) cannot be overwritten by `cat >` - remove it first.
if [ -d "$INCLUDE" ]; then
  echo "[deploy] removing directory squatting on $INCLUDE"
  rm -rf "$INCLUDE"
  INCLUDE_WAS_DIR=1
else
  INCLUDE_WAS_DIR=0
fi
cat > "$INCLUDE" <<EOF
# ACTIVE BLUE-GREEN COLOR: $COLOR (generated by scripts/deploy-nas.sh).
# Included from deploy/nginx.conf; applied below via an nginx restart.

upstream roboco_panel {
    server roboco-panel-$COLOR:3000;
}

upstream roboco_orchestrator {
    server roboco-orchestrator-$COLOR:8000;
}

upstream roboco_dispatcher {
    server roboco-dispatcher-$COLOR:8000;
}
EOF

if [ ! -f "$INCLUDE" ]; then
  echo "[deploy] FATAL: $INCLUDE still not a regular file" >&2
  exit 1
fi

# The include was a directory when nginx's mount was created: that running
# container is pinned to the stale inode. Recreate it so the bind-mount
# picks up the real file. Otherwise a hot reload applies the change with
# zero dropped requests.
if [ "$INCLUDE_WAS_DIR" -eq 1 ] &&
   [ "$("${COMPOSE[@]}" ps -q nginx)" != "" ]; then
  echo "[deploy] recreating nginx to drop the stale directory mount..."
  "${COMPOSE[@]}" up -d --force-recreate nginx
  wait_healthy roboco-nginx || wait_healthy roboco-nginx
  sleep 2
fi

if ! "${COMPOSE[@]}" ps -q nginx | grep -q .; then
  "${COMPOSE[@]}" up -d nginx
fi

# Stop the previous color BEFORE touching nginx: nginx resolves the
# upstream hostnames in the include at config load, so a hot reload here
# leaves workers pointing at the already-stopped color and it trips after
# the deploy (2026-09-17). A full restart re-resolves DNS.
if [ -n "$("${COMPOSE[@]}" ps -q "orchestrator-$OTHER")" ]; then
  echo "[deploy] stopping $OTHER (kept for rollback: re-run with --color $OTHER)..."
  "${COMPOSE[@]}" stop "orchestrator-$OTHER" "dispatcher-$OTHER" "indexer-$OTHER" "panel-$OTHER"
fi

echo "[deploy] restarting nginx to pick up $COLOR (re-resolves upstreams)..."
"${COMPOSE[@]}" exec -T nginx nginx -t
"${COMPOSE[@]}" restart nginx
wait_healthy roboco-nginx || wait_healthy roboco-nginx

echo "[deploy] current state:"
"${COMPOSE[@]}" ps
echo "[deploy] done. Active color: $COLOR (traffic switched, $OTHER stopped for rollback)."
