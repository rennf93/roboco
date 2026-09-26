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
      # No healthcheck defined (nginx): running IS the bar. Treating this
      # as success is what keeps the flip from "hanging" in a pointless
      # 2x POLL_TIMEOUT loop (2026-09-17).
      running/none) echo "[deploy] $c running (no healthcheck)"; return 0 ;;
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
  # Non-fatal: a total image wipe also takes roboco-agent-base down with the
  # app images (2026-09-25); the ensure passes below rebuild everything in
  # dependency order. Killing the deploy here would defeat that.
  "${COMPOSE[@]}" build "orchestrator-$COLOR" "panel-$COLOR" ||
    echo "[deploy] WARNING: app/$COLOR pre-build failed; the ensure pass below will rebuild" >&2
fi

# Ensure ALL needed images exist BEFORE the new color comes up: the overlap
# window (old color serving, new color starting) must never stretch into
# 10-minute image builds, and spawns/first-ups must never discover a missing
# image at runtime (the orchestrator/dispatcher containers have no source
# tree, so a build they trigger fails with "unable to prepare context: path
# /volume1/roboco not found" - 2026-09-17, secretary/prompter live chats
# after the rebuild wiped the old agent images). Everything is ensured HERE,
# on the host, where the context exists. Idempotent.
echo "[deploy] ensuring images: pull pass (core + $COLOR)..."
while IFS= read -r img; do
  [ -z "$img" ] && continue
  # The rollback color's app images are rebuilt by its own re-run.
  case "$img" in *"-$OTHER") continue ;; esac
  if docker image inspect "$img" >/dev/null 2>&1; then
    echo "[deploy] $img present, skipping"
  elif docker pull -q "$img" >/dev/null 2>&1; then
    echo "[deploy] $img pulled"
  elif [ "${img#roboco-}" != "$img" ]; then
    # Expected: locally built, the build pass below produces it.
    echo "[deploy] $img not pullable (locally built)"
  else
    # Say it NOW, loudly: a silently-failed pull here only surfaces much
    # later as an obscure "unauthorized" at container creation (the
    # quay.io/minio 401, 2026-09-25).
    echo "[deploy] WARNING: $img not present and pull failed; up will fail unless pulled/built later" >&2
  fi
  # Not present and not pullable = locally built; the build pass below
  # handles it (or the build step above already did).
done < <("${COMPOSE[@]}" config --images)

echo "[deploy] ensuring images: build pass (missing locally-built images)..."
# Wipe-proof fixpoint. roboco-agent-base is the root of the agent image DAG:
# every role image FROMs it, so after a full image wipe all role builds fail
# until the base exists, and a single-pass, die-on-first-failure loop can
# never recover (2026-09-25). Build the base first, then repeat rounds over
# every missing image until a round adds nothing new. One immediate retry
# per build absorbs transient download resets (dl.k8s.io reset 7 of 8 TLS
# handshakes from this NAS that day).
if ! docker image inspect roboco-agent-base >/dev/null 2>&1; then
  echo "[deploy] building roboco-agent-base (root of the agent image DAG) ..."
  docker build -q -t roboco-agent-base:latest -f docker/agent-base.Dockerfile . ||
    docker build -q -t roboco-agent-base:latest -f docker/agent-base.Dockerfile .
fi
for round in 1 2 3 4 5 6; do
  built=0
  while IFS= read -r img; do
    [ -z "$img" ] && continue
    # The rollback color's app images are rebuilt by its own re-run.
    case "$img" in *"-$OTHER") continue ;; esac
    docker image inspect "$img" >/dev/null 2>&1 && continue
    case "$img" in
      roboco-*)
        name="${img#roboco-}"
        case "$name" in
          # Bare blue names are built by their own deploy run.
          orchestrator|panel) continue ;;
          orchestrator-*|panel-*)
            echo "[deploy] building $img (compose) ..."
            if "${COMPOSE[@]}" build "$name" || "${COMPOSE[@]}" build "$name"; then
              built=$((built + 1))
            else
              echo "[deploy] WARNING: compose build failed for $img (retried next round)" >&2
            fi
            ;;
          *)
            if [ -f "docker/$name.Dockerfile" ]; then
              echo "[deploy] building $img (round $round) ..."
              if docker build -q -t "$img:latest" -f "docker/$name.Dockerfile" . ||
                 docker build -q -t "$img:latest" -f "docker/$name.Dockerfile" .; then
                built=$((built + 1))
              else
                echo "[deploy] WARNING: build failed for $img (retried next round)" >&2
              fi
            else
              echo "[deploy] WARNING: $img missing and no docker/$name.Dockerfile to build it" >&2
            fi
            ;;
        esac
        ;;
    esac
  done < <("${COMPOSE[@]}" config --images)
  [ "$built" -eq 0 ] && break
done

echo "[deploy] ensuring images: reconciliation..."
while IFS= read -r img; do
  [ -z "$img" ] && continue
  case "$img" in *"-$OTHER") continue ;; esac
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    echo "[deploy] WARNING: $img still missing after pull+build passes" >&2
  fi
done < <("${COMPOSE[@]}" config --images)

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

echo "[deploy] schema migrations (idempotent)..."
"${COMPOSE[@]}" exec -T "orchestrator-$COLOR" alembic upgrade head ||
  echo "[deploy] WARNING: alembic failed; traffic NOT flipped, investigate before flipping"

echo "[deploy] switching traffic to $COLOR..."
mkdir -p front
# A directory squatting on the include path (from an nginx start that
# preceded the file) cannot be replaced by a file write - remove it first.
if [ -d "$INCLUDE" ]; then
  echo "[deploy] removing directory squatting on $INCLUDE"
  rm -rf "$INCLUDE"
fi
# Keep the current include as the abort-rollback copy: if the NEW include
# fails nginx's config test, the old color must keep serving.
if [ -f "$INCLUDE" ]; then
  cp "$INCLUDE" "$INCLUDE.prev"
fi
# Atomic swap (tmp + mv): nginx reads the include through the front/
# DIRECTORY bind, so the mv's inode swap is visible to the running
# container - a plain `cat >` on a file-bound path can leave the container
# pinned to the old inode (2026-09-17 green flip).
# `set` directives (NOT upstream blocks): with the resolver in
# deploy/nginx.conf, these resolve at request time, so the include can
# never crash nginx on a not-yet-running color and a flip is a hot reload.
cat > "front/.active-upstreams.conf.tmp" <<EOF
# ACTIVE BLUE-GREEN COLOR: $COLOR (generated by scripts/deploy-nas.sh).
# Included from deploy/nginx.conf via the front/ directory mount.
set \$active_panel roboco-panel-$COLOR:3000;
set \$active_orchestrator roboco-orchestrator-$COLOR:8000;
set \$active_dispatcher roboco-dispatcher-$COLOR:8000;
EOF
mv -f "front/.active-upstreams.conf.tmp" "$INCLUDE"

# Validate BEFORE stopping the previous color: a failed test here must
# never leave both colors down. On failure, restore the previous include
# and abort with traffic untouched.
if [ "$("${COMPOSE[@]}" ps -q nginx)" != "" ]; then
  if ! "${COMPOSE[@]}" exec -T nginx nginx -t; then
    # The rendered config inside the container can predate a template update
    # (envsubst runs only at container start), e.g. the one-time migration to
    # the set-directive include (2026-09-17). Recreate ONCE to re-render,
    # then test again before giving up.
    echo "[deploy] config test failed; recreating nginx once to re-render the template..."
    "${COMPOSE[@]}" up -d --force-recreate nginx
    wait_healthy roboco-nginx || true
    sleep 1
    if ! "${COMPOSE[@]}" exec -T nginx nginx -t; then
      if [ -f "$INCLUDE.prev" ]; then
        echo "[deploy] nginx config test FAILED for $COLOR include; restoring previous color's include" >&2
        cp "$INCLUDE.prev" "$INCLUDE"
        "${COMPOSE[@]}" exec -T nginx nginx -s reload 2>/dev/null || true
      fi
      echo "[deploy] FATAL: traffic NOT switched. Previous color still serving; investigate the include above." >&2
      exit 1
    fi
  fi
  # Hot reload: new workers pick up $COLOR immediately, old workers drain
  # their in-flight requests against the previous color. No restart, no
  # dropped requests, and no dependency on the previous color still running
  # (variables resolve per request, so DNS can't fail the reload).
  echo "[deploy] hot-reloading nginx onto $COLOR..."
  "${COMPOSE[@]}" exec -T nginx nginx -s reload ||
    { echo "[deploy] FATAL: nginx -s reload failed; previous color still serving" >&2; exit 1; }
else
  # Cold start (first deploy): up -d renders the template with the fresh
  # include already in place.
  "${COMPOSE[@]}" up -d nginx
fi

# Drain the previous color AFTER the reload: the old nginx workers have
# finished handing off, so stopping it now drops nothing.
if [ -n "$("${COMPOSE[@]}" ps -q "orchestrator-$OTHER")" ]; then
  echo "[deploy] stopping $OTHER (kept for rollback: re-run with --color $OTHER)..."
  "${COMPOSE[@]}" stop "orchestrator-$OTHER" "dispatcher-$OTHER" "indexer-$OTHER" "panel-$OTHER"
fi

wait_healthy roboco-nginx || wait_healthy roboco-nginx

echo "[deploy] current state:"
"${COMPOSE[@]}" ps
echo "[deploy] done. Active color: $COLOR (traffic switched, $OTHER stopped for rollback)."
