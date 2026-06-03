#!/usr/bin/env bash
# Reset the smoke-test runtime state (tasks, sessions, messages, journals,
# journal entries, notifications, audit, waiting, work_sessions, groups)
# while preserving the project scaffolding (agents, projects, channels).
#
# Also resets agent git workspaces to a clean state on the default
# branch — uncommitted edits or stale feature branches from prior runs
# trap agents that try to claim a fresh task (checkout fails on dirty
# tree, branch names collide).
#
# Works from the host or from inside the orchestrator container — the
# script auto-detects.
#
# Usage:
#   ./scripts/reset_runtime_state.sh           # local docker
#   ssh renzof-nas.local "sudo bash -s" < scripts/reset_runtime_state.sh
#                                               # remote over ssh
#
# Env:
#   WORKSPACES_ROOT — override workspace root (default: tries
#     /volume1/roboco/data/workspaces then /data/workspaces; skip reset
#     if none exist).
#   SKIP_WORKSPACE_RESET=1 — skip the workspace cleanup step entirely
#     (DB + Redis only).

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

# Stop all running agent containers first so they don't keep writing to
# the DB while we're wiping it (journal FK violations, task-not-found, etc.).
# `grep` returns 1 when no matches; combined with `set -o pipefail` + `set -e`
# that would abort the entire script before the SQL wipe ever runs. Capture
# the list with `|| true` so a no-match exit is benign.
echo ">>> Stopping all agent containers..."
agents_running=$($DOCKER ps --format '{{.Names}}' | grep '^roboco-agent-' || true)
if [ -n "$agents_running" ]; then
    while IFS= read -r container; do
        echo "    Stopping $container"
        $DOCKER stop -t 5 "$container" >/dev/null 2>&1 || true
        $DOCKER rm -f "$container" >/dev/null 2>&1 || true
    done <<< "$agents_running"
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

# Workspace reset — each agent has a private git clone at
# {root}/{project}/{team}/{agent}. Leftover staged/untracked edits and
# feature branches from a previous run will fail the claim→start
# workflow (the `roboco_task_start` handler aborts on UNCOMMITTED_CHANGES
# and branch create collides with an already-existing feature branch).
# We bring each clone back to origin/<default> with a hard reset +
# `git clean -fdx` + local feature-branch prune.
if [ "${SKIP_WORKSPACE_RESET:-0}" = "1" ]; then
    echo ">>> Skipping workspace reset (SKIP_WORKSPACE_RESET=1)."
    echo ">>> Done."
    exit 0
fi

# Resolve workspace root. Explicit env wins; then the NAS path; then the
# in-container path. If none exist, skip the reset (fresh deploy —
# there's nothing to clean yet).
_resolve_workspaces_root() {
    if [ -n "${WORKSPACES_ROOT:-}" ]; then
        echo "$WORKSPACES_ROOT"
        return
    fi
    for candidate in /volume1/roboco/data/workspaces /data/workspaces; do
        if [ -d "$candidate" ]; then
            echo "$candidate"
            return
        fi
    done
    echo ""
}

WORKSPACES_ROOT=$(_resolve_workspaces_root)
if [ -z "$WORKSPACES_ROOT" ]; then
    echo ">>> No workspaces directory found — skipping workspace reset."
    echo ">>> Done."
    exit 0
fi

echo ">>> Resetting agent workspaces under $WORKSPACES_ROOT ..."

_reset_workspace() {
    local workspace="$1"
    if [ ! -d "$workspace/.git" ]; then
        return
    fi

    # Each clone is owned by the agent uid (typically the invoking
    # user on the NAS, uid 1000 inside containers). Running git as
    # that user keeps .git/logs from being rewritten root-owned.
    local owner
    owner=$(stat -c '%U' "$workspace" 2>/dev/null || stat -f '%Su' "$workspace")
    local as_owner=""
    if command -v sudo >/dev/null 2>&1 && [ "$owner" != "$(id -un)" ]; then
        as_owner="sudo -u $owner"
    fi

    # Find default branch once — `git remote show origin` can fail if
    # the repo has no origin or auth is missing (private clones keep no
    # persisted PAT), so fall back to main, then master. We swallow both
    # stderr AND the exit code: under `set -e` + `pipefail` a 128 from a
    # credential-less fetch would otherwise abort the whole reset before
    # the local-branch fallback below.
    local default
    default=$(
        { $as_owner git -C "$workspace" remote show origin 2>/dev/null \
            | awk '/HEAD branch/{print $3}'; } || true
    )
    if [ -z "$default" ] || [ "$default" = "(unknown)" ]; then
        if $as_owner git -C "$workspace" show-ref --verify --quiet refs/heads/main; then
            default="main"
        elif $as_owner git -C "$workspace" show-ref --verify --quiet refs/heads/master; then
            default="master"
        else
            echo "    SKIP $workspace (no default branch resolved)"
            return
        fi
    fi

    # Fetch is best-effort — if origin is unreachable we still want to
    # reset locally so the next claim isn't blocked by dirty state.
    $as_owner git -C "$workspace" fetch --prune --quiet origin 2>/dev/null || true
    $as_owner git -C "$workspace" reset --hard --quiet HEAD 2>/dev/null || true
    $as_owner git -C "$workspace" checkout --quiet "$default" 2>/dev/null \
        || $as_owner git -C "$workspace" checkout --quiet -B "$default" "origin/$default" 2>/dev/null \
        || true
    # Align the default branch with the remote; swallow failures when
    # origin isn't reachable (we're still better off than a dirty tree).
    $as_owner git -C "$workspace" reset --hard --quiet "origin/$default" 2>/dev/null || true
    $as_owner git -C "$workspace" clean -fdx --quiet 2>/dev/null || true

    # Delete every local branch except the default — these are
    # smoke-test feature branches that would otherwise collide with
    # next-run branch creation (`fatal: branch already exists`).
    # `grep -v` returning 1 when only the default exists is normal;
    # `|| true` on the pipe prevents `pipefail` + `set -e` from
    # aborting on a clean workspace.
    local all_branches stray_branches
    all_branches=$(
        $as_owner git -C "$workspace" for-each-ref \
            --format='%(refname:short)' refs/heads 2>/dev/null || true
    )
    stray_branches=$(echo "$all_branches" | grep -vxF "$default" || true)
    if [ -n "$stray_branches" ]; then
        while IFS= read -r branch; do
            [ -z "$branch" ] && continue
            $as_owner git -C "$workspace" branch -D --quiet "$branch" \
                2>/dev/null || true
        done <<< "$stray_branches"
    fi

    echo "    OK   $workspace (→ $default)"
}

# Workspace layout is {root}/{project}/{team}/{agent} — depth 3 under
# WORKSPACES_ROOT. Null-separated output handles paths with spaces.
count=0
while IFS= read -r -d '' workspace; do
    _reset_workspace "$workspace"
    count=$((count + 1))
done < <(find "$WORKSPACES_ROOT" -mindepth 3 -maxdepth 3 -type d -print0)

if [ "$count" -eq 0 ]; then
    echo "    (no workspaces to reset)"
fi

echo ">>> Done."
