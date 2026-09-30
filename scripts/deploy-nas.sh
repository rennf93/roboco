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

echo "[deploy] app/$COLOR: content-gated image builds..."
# ---------------------------------------------------------------------------
# Content-aware rebuild gating (2026-09-28). The 02ec8b4c always-rebuild rule
# fixed the stale-image outage by rebuilding EVERYTHING on every deploy -
# correct, but a zero-change redeploy re-ran the whole 25-image agent matrix
# and a panel-only change rebuilt agents that had nothing to do with it. Now
# every image family carries a content hash of its REAL inputs, persisted
# per-image in .deploy-cache/, and a build is skipped when the image exists
# AND its recorded hash matches the checkout:
#   - roboco/ + pyproject + uv.lock changes  -> orchestrator family + all
#     agent images rebuild (they bake the roboco venv) - panel does NOT.
#   - panel/ changes -> panel rebuilds only.
#   - docker/ changes (Dockerfiles, entrypoints) -> everything rebuilds.
# Missing images always build (the fixpoint pass below is unchanged, so the
# 2026-09-25 total-wipe recovery still works). The global CONTEXT_HASH label
# stamped on builds stays as provenance; freshness is decided here, per
# family, from the hash files. Under-inclusive hashes would rot images, so
# the families are deliberately coarse: docker/ as ONE input covers every
# Dockerfile and entrypoint, at the cost of a docker-only change rebuilding
# the matrix - rare, and always safe.
# ---------------------------------------------------------------------------
CACHE_DIR="${STACK_DIR:-/volume1/roboco}/.deploy-cache"
mkdir -p "$CACHE_DIR"

hash_paths() { # hash_paths <paths...> -> 16-hex over every file's bytes
  local h
  h="$(find "$@" -type f \
      -not -path '*/__pycache__/*' -not -path '*/node_modules/*' \
      -not -path '*/.next/*' -not -path '*/.git/*' -not -name '*.pyc' \
      -print0 2>/dev/null | LC_ALL=C sort -z | xargs -0 -r cat 2>/dev/null \
      | sha256sum | cut -c1-16 || true)"
  echo "${h:-unknown}"
}
ROBOCO_HASH=$(hash_paths roboco pyproject.toml uv.lock README.md)
PANEL_HASH=$(hash_paths panel)
DOCKER_HASH=$(hash_paths docker)
echo "[deploy] content hashes: roboco=$ROBOCO_HASH panel=$PANEL_HASH docker=$DOCKER_HASH"

family_hash() { # family_hash <agent|orch|panel>
  case "$1" in
    agent) printf '%s' "agent $ROBOCO_HASH $DOCKER_HASH" ;;
    orch)  printf '%s' "orch $ROBOCO_HASH $DOCKER_HASH" ;;
    panel) printf '%s' "panel $PANEL_HASH $DOCKER_HASH" ;;
  esac | sha256sum | cut -c1-16
}

image_family() { # image_family <name-without-roboco-prefix>
  case "$1" in
    panel*) echo panel ;;
    orchestrator*|indexer*|dispatcher*|decisions*|video-renderer*|sandbox*)
      echo orch ;;
    *) echo agent ;;
  esac
}

image_fresh() { # image_fresh <img> <name>: exit 0 = up to date, 1 = build
  docker image inspect "$img" >/dev/null 2>&1 || return 1
  local f="$CACHE_DIR/$name.hash" expected
  expected=$(family_hash "$(image_family "$name")")
  [ -f "$f" ] && [ "$(cat "$f" 2>/dev/null)" = "$expected" ]
}

record_fresh() { family_hash "$(image_family "$name")" > "$CACHE_DIR/$name.hash"; }

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

echo "[deploy] ensuring images: content-gated build pass (only stale or missing)..."
# Provenance stamp: a CONTENT hash of the checkout, deliberately NOT a git
# ref (the deploy checkout carries no branch identity). Stamped on builds so
# a running image can always be traced to the bytes that produced it.
# Null-delimited end to end: the checkout contains human files with spaces
# (roboco/vault_assets/meta/"Sync to your Mac.md") and whitespace-splitting
# xargs turned each word into a missing file, failing the hash - which under
# set -euo pipefail killed the whole deploy silently. The || true degrades a
# failed hash to "unknown" instead of ever killing a deploy.
CONTEXT_HASH="$(find roboco docker pyproject.toml uv.lock README.md \
  -type f -print0 2>/dev/null | LC_ALL=C sort -z | xargs -0 -r cat 2>/dev/null \
  | sha256sum | cut -c1-12 || true)"
[ -n "$CONTEXT_HASH" ] || CONTEXT_HASH="unknown"
HASH_LABEL="--label org.opencontainers.image.checkout=$CONTEXT_HASH"
echo "[deploy] checkout content hash: $CONTEXT_HASH"
if [ "$SKIP_BUILD" -eq 0 ]; then
  # agent-base is the root of the agent image DAG: build it first when stale
  # so every dependent's build sees the fresh base (compose config --images
  # ordering is not trusted for this).
  if image_fresh roboco-agent-base agent-base; then
    echo "[deploy] roboco-agent-base up to date, skipping"
  else
    echo "[deploy] building roboco-agent-base (root of the agent image DAG) ..."
    docker build -q -t roboco-agent-base:latest $HASH_LABEL -f docker/agent-base.Dockerfile . ||
      docker build -q -t roboco-agent-base:latest $HASH_LABEL -f docker/agent-base.Dockerfile .
    name="agent-base"
    record_fresh
  fi
  while IFS= read -r img; do
    [ -z "$img" ] && continue
    # The rollback color's app images are rebuilt by its own re-run.
    case "$img" in *"-$OTHER") continue ;; esac
    case "$img" in
      roboco-*)
        name="${img#roboco-}"
        case "$name" in
          # Bare blue names are built by their own deploy run.
          orchestrator|panel) continue ;;
        esac
        df="docker/${name%-blue}.Dockerfile"
        df="${df%-green}.Dockerfile"
        if [ ! -f "$df" ]; then
          continue # registry-pulled or compose-shared image (e.g. dispatcher)
        fi
        if image_fresh "$img" "$name"; then
          echo "[deploy] $img up to date, skipping"
          continue
        fi
        case "$name" in
          orchestrator-*|panel-*)
            # Compose-defined builds (the live color's app services).
            echo "[deploy] building $img (compose) ..."
            if "${COMPOSE[@]}" build "$name" || "${COMPOSE[@]}" build "$name"; then
              record_fresh
            else
              echo "[deploy] WARNING: compose build failed for $img" >&2
            fi
            ;;
          *)
            echo "[deploy] building $img ..."
            if docker build -q -t "$img:latest" $HASH_LABEL -f "$df" . ||
               docker build -q -t "$img:latest" $HASH_LABEL -f "$df" .; then
              record_fresh
            else
              echo "[deploy] WARNING: build failed for $img" >&2
            fi
            ;;
        esac
        ;;
    esac
  done < <("${COMPOSE[@]}" config --images)
else
  echo "[deploy] --skip-build: trusting existing images (fixpoint still recovers missing)"
fi

# The -live (interactive chat) images are spawned by the orchestrator at
# runtime, never referenced by a compose service, so the compose passes never
# see them - they silently rotted (roboco-agent-hummin-live served Sep 18 code
# for 10 days while the orchestrator moved ahead, 2026-09-28 intake outage).
# Every -live Dockerfile builds FROM a provider agent image, so a live build is
# only attempted when that base exists locally: after a wipe the fixpoint below
# rebuilds the bases first, and a base that survived all its rounds is a loud,
# named skip instead of a cryptic docker.io "pull access denied" (2026-09-28
# Deploy #2: the live pass sat between the content and fixpoint passes, ran
# while the wiped provider bases were still missing, and every FROM fell
# through to docker.io). Callers must run after HASH_LABEL is set (it stamps
# the builds); $name is set globally because record_fresh reads it.

live_base_of() { # live_base_of <dockerfile> -> the FROM target
  awk '/^FROM/{print $2; exit}' "$1"
}

live_build_one() { # live_build_one <dockerfile> <gate|missing>
  local df="$1" mode="$2" img base
  name="${df#docker/}"
  name="${name%.Dockerfile}" # e.g. agent-hummin-live
  img="roboco-$name"
  if [ "$mode" = missing ] && docker image inspect "$img" >/dev/null 2>&1; then
    return 0 # present: only the content gate rebuilds stale live images
  fi
  if image_fresh "$img" "$name"; then
    echo "[deploy] $img up to date, skipping"
    return 0
  fi
  base=$(live_base_of "$df")
  if [ "${base#roboco-}" != "$base" ] &&
     ! docker image inspect "$base" >/dev/null 2>&1; then
    echo "[deploy] WARNING: $img deferred: base $base missing (fixpoint must build it first)" >&2
    return 1
  fi
  echo "[deploy] building $img (live chat) ..."
  if docker build -q -t "$img:latest" $HASH_LABEL -f "$df" . ||
     docker build -q -t "$img:latest" $HASH_LABEL -f "$df" .; then
    record_fresh
    return 0
  fi
  echo "[deploy] WARNING: build failed for $img" >&2
  return 1
}

echo "[deploy] ensuring images: fixpoint pass (wipe recovery: retry anything still missing)..."
for round in 1 2 3 4 5 6; do
  built=0
  while IFS= read -r img; do
    [ -z "$img" ] && continue
    case "$img" in *"-$OTHER") continue ;; esac
    docker image inspect "$img" >/dev/null 2>&1 && continue
    case "$img" in
      roboco-*)
        name="${img#roboco-}"
        case "$name" in
          orchestrator|panel) continue ;;
          orchestrator-*|panel-*)
            echo "[deploy] building $img (compose, round $round) ..."
            if "${COMPOSE[@]}" build "$name" || "${COMPOSE[@]}" build "$name"; then
              record_fresh
              built=$((built + 1))
            else
              echo "[deploy] WARNING: compose build failed for $img (retried next round)" >&2
            fi
            ;;
          *)
            if [ -f "docker/$name.Dockerfile" ]; then
              echo "[deploy] building $img (round $round) ..."
              if docker build -q -t "$img:latest" $HASH_LABEL -f "docker/$name.Dockerfile" . ||
                 docker build -q -t "$img:latest" $HASH_LABEL -f "docker/$name.Dockerfile" .; then
                record_fresh
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
  # -live images are not in compose config --images: recover them in the same
  # rounds, so a live-chat spawn never finds its image missing after this
  # deploy (and --skip-build still recovers them after a wipe).
  for df in docker/agent-*-live.Dockerfile; do
    [ -e "$df" ] || continue
    name="${df#docker/}"
    name="${name%.Dockerfile}"
    img="roboco-$name"
    docker image inspect "$img" >/dev/null 2>&1 && continue
    if live_build_one "$df" missing &&
       docker image inspect "$img" >/dev/null 2>&1; then
      built=$((built + 1))
    fi
  done
  [ "$built" -eq 0 ] && break
done

if [ "$SKIP_BUILD" -eq 0 ]; then
  # AFTER the fixpoint, not wedged between the content and fixpoint passes
  # (see live_build_one above): every provider base is by now rebuilt or
  # loudly failed, so a live build here either succeeds, skips as fresh, or
  # skips with a named missing base.
  echo "[deploy] ensuring images: live-chat pass (runtime-spawned, not in compose)..."
  for df in docker/agent-*-live.Dockerfile; do
    [ -e "$df" ] || continue
    live_build_one "$df" gate || true
  done
fi

echo "[deploy] ensuring images: reconciliation..."
while IFS= read -r img; do
  [ -z "$img" ] && continue
  case "$img" in *"-$OTHER") continue ;; esac
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    echo "[deploy] WARNING: $img still missing after pull+build passes" >&2
  fi
done < <("${COMPOSE[@]}" config --images)
for df in docker/agent-*-live.Dockerfile; do
  [ -e "$df" ] || continue
  name="${df#docker/}"
  name="${name%.Dockerfile}"
  docker image inspect "roboco-$name" >/dev/null 2>&1 ||
    echo "[deploy] WARNING: roboco-$name still missing after pull+build passes (live-chat spawns will fail)" >&2
done

echo "[deploy] bringing up $COLOR ..."
# Named-service up: starts ONLY this generation (+ its dependencies: core,
# agent builders). Blue (no-profile) is in every model, so a plain
# `--profile green up` would reconcile blue too - recreating it with new
# code and destroying rollback honesty. nginx joins the blue list (first
# deploy) and is ensured for green (it is the reload target).
if [ "$COLOR" = "green" ]; then
  "${COMPOSE[@]}" up -d --build orchestrator-green dispatcher-green indexer-green panel-green decisions
  "${COMPOSE[@]}" up -d nginx
else
  "${COMPOSE[@]}" up -d --build orchestrator-blue dispatcher-blue indexer-blue panel-blue decisions nginx
fi
wait_healthy "roboco-orchestrator-$COLOR"
wait_healthy roboco-postgres
wait_healthy roboco-ollama
wait_healthy roboco-decisions
wait_healthy roboco-ollama-init exit0

echo "[deploy] schema migrations (idempotent)..."
# The flip is GATED on migrations: the message below used to say "traffic
# NOT flipped" and then flip anyway - a failed upgrade deployed the new
# color against an unmigrated schema (the exact deploy-bites-back class
# this script exists to prevent). Old color keeps serving; re-run deploys.
if ! "${COMPOSE[@]}" exec -T "orchestrator-$COLOR" alembic upgrade head; then
  echo "[deploy] FATAL: alembic upgrade failed; traffic NOT switched (old color still serving). Investigate, then re-run." >&2
  exit 1
fi

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
