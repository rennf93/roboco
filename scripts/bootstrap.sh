#!/usr/bin/env bash
# One-command bring-up for the registry (pull-and-run) deploy — the
# `make quickstart` target. Idempotent: a missing .env is created and
# populated with freshly generated secrets; an existing .env is reused
# byte-for-byte (never touched). Then `pull` + `up -d`, then a doctor-style
# readiness sweep with a bounded timeout, failing loud with an actionable
# message at whichever stage doesn't check out.
#
# Grounded probes (don't guess at endpoints — see docker/nginx.conf and
# roboco/api/routes/health.py):
#   - The health check is GET /health at the ROOT (no /api prefix) — the
#     health router is mounted with none. GET /api/health genuinely 404s;
#     nginx itself only proxies literal /health (and /ready) unauthenticated
#     straight to the orchestrator.
#   - GET /api/auth/status is ALWAYS mounted, unauthenticated regardless of
#     ROBOCO_AGENT_AUTH_REQUIRED (roboco/api/auth/routes.py — "always
#     available probe; the panel's middleware gates on this"), so it's a
#     reliable second 200 that exercises the nginx -> /api/ -> orchestrator
#     path end to end, independent of /health's simpler direct route.
#   - DB migration-head is logged by roboco/db/base.py's run_migrations()
#     ("Alembic upgrade finished") — there is no dedicated status endpoint,
#     so we grep the orchestrator container's own log for that line.
#   - Ollama model presence mirrors what the ollama-init one-shot itself
#     verifies (docker-compose.registry.yml): `ollama list` naming both
#     qwen3-embedding and glm-5.2.
#
# ROBOCO_PANEL_AGENT_TOKEN is a standing CEO credential (see .env.example) —
# minted here whether or not cloud auth is armed, because docker-compose.
# registry.yml's `${ROBOCO_PANEL_AGENT_TOKEN:?...}` refuses an EMPTY value
# exactly like an unset one, unconditionally (verified: setting
# ROBOCO_CLOUD_AUTH_ENABLED=true alongside an empty token still fails `docker
# compose config` — the `:?` message's "unless ROBOCO_CLOUD_AUTH_ENABLED=true"
# is documentation, not encoded logic). Blanking it is not currently an option
# that lets the stack start; quickstart mints it and warns loudly instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

COMPOSE_FILE="docker-compose.registry.yml"
ENV_FILE=".env"
ENV_EXAMPLE=".env.example"
BASE_URL="http://localhost:3000"
# Well-known CEO identity (roboco/foundation/identity.py — "0000-0000:
# System sentinel + CEO"), used only to derive the panel token below.
CEO_AGENT_ID="00000000-0000-0000-0000-000000000001"

TIMEOUT_SECONDS="${ROBOCO_BOOTSTRAP_TIMEOUT:-300}"
POLL_INTERVAL_SECONDS="${ROBOCO_BOOTSTRAP_POLL_INTERVAL:-5}"

log() {
    echo "[bootstrap] $(date -u -Iseconds) $*"
}

fail() {
    echo "[bootstrap] $(date -u -Iseconds) FATAL: $*" >&2
    exit 1
}

# require_nonempty_env_var <VAR> <remedy hint> — fails loud, naming the
# exact missing var and how to fix it, instead of handing off to docker
# compose's own opaque interpolation error later.
require_nonempty_env_var() {
    local var="$1" hint="$2" value
    value="$(grep -E "^${var}=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2-)"
    [ -n "$value" ] \
        || fail "${ENV_FILE} has no value for ${var} (docker-compose.registry.yml requires it non-empty to start). ${hint}"
}

# retry_until <deadline_epoch> <cmd...> — polls a boolean command every
# POLL_INTERVAL_SECONDS until it succeeds or the deadline passes.
retry_until() {
    local deadline="$1"
    shift
    while true; do
        if "$@"; then
            return 0
        fi
        if [ "$(date +%s)" -ge "$deadline" ]; then
            return 1
        fi
        sleep "$POLL_INTERVAL_SECONDS"
    done
}

# --- Stage 0: preflight -----------------------------------------------------

command -v docker >/dev/null 2>&1 \
    || fail "docker not found on PATH. Install Docker: https://docs.docker.com/get-docker/"

docker info >/dev/null 2>&1 \
    || fail "Docker daemon not reachable (is Docker running? do you have permission to use it?)."

docker compose version >/dev/null 2>&1 \
    || fail "'docker compose' (v2 plugin) not found. Install/update Docker Desktop, or the docker-compose-plugin package."

command -v curl >/dev/null 2>&1 \
    || fail "curl not found on PATH — required to poll the health endpoints. Install curl and re-run."

log "Docker reachable: $(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo unknown)"

# --- Stage 1: .env ----------------------------------------------------------

if [ -f "$ENV_FILE" ]; then
    log "Reusing existing ${ENV_FILE} (left untouched)."
    require_nonempty_env_var ROBOCO_ENCRYPTION_KEY \
        "Generate: python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
    require_nonempty_env_var ROBOCO_AGENT_AUTH_SECRET \
        "Generate: python3 -c 'import secrets; print(secrets.token_hex(32))'"
    require_nonempty_env_var ROBOCO_PANEL_AGENT_TOKEN \
        "Mint with 'make panel-token' (after ROBOCO_AGENT_AUTH_SECRET is set) — see .env.example. Required even with cloud auth armed; docker-compose.registry.yml refuses an empty value either way."
else
    [ -f "$ENV_EXAMPLE" ] || fail "${ENV_EXAMPLE} not found — can't scaffold a fresh ${ENV_FILE}."

    command -v python3 >/dev/null 2>&1 \
        || fail "python3 not found on PATH — required to generate the secrets a fresh ${ENV_FILE} needs (ROBOCO_ENCRYPTION_KEY, ROBOCO_AGENT_AUTH_SECRET, ROBOCO_PANEL_AGENT_TOKEN). Install python3, or copy ${ENV_EXAMPLE} to ${ENV_FILE} yourself and fill those in (see the generation one-liners documented next to each in ${ENV_EXAMPLE}; ROBOCO_PANEL_AGENT_TOKEN can also be minted later with 'make panel-token' once ROBOCO_AGENT_AUTH_SECRET is set)."

    cp "$ENV_EXAMPLE" "$ENV_FILE"
    log "Created ${ENV_FILE} from ${ENV_EXAMPLE}."

    # Does this .env (or the shell environment quickstart was invoked from)
    # ask for cloud auth? Checked so the post-generation warning below can
    # call out the standing-credential conflict specifically, not just as a
    # generic footnote.
    CLOUD_AUTH_HINT=false
    if grep -qE '^ROBOCO_CLOUD_AUTH_ENABLED=true$' "$ENV_FILE" 2>/dev/null \
        || [ "${ROBOCO_CLOUD_AUTH_ENABLED:-}" = "true" ]; then
        CLOUD_AUTH_HINT=true
    fi

    # The exact one-liners documented in .env.example, next to each var.
    if ! ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"; then
        rm -f "$ENV_FILE"
        fail "Could not generate ROBOCO_ENCRYPTION_KEY (python3 -c 'from cryptography.fernet import Fernet; ...' failed above). Is the 'cryptography' package installed for this python3 (pip install cryptography)? ${ENV_FILE} removed — re-run once fixed."
    fi
    if ! AGENT_AUTH_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"; then
        rm -f "$ENV_FILE"
        fail "Could not generate ROBOCO_AGENT_AUTH_SECRET (python3 -c 'import secrets; ...' failed above). ${ENV_FILE} removed — re-run once fixed."
    fi

    # docker-compose.registry.yml's nginx service hard-requires
    # ROBOCO_PANEL_AGENT_TOKEN non-empty (`${VAR:?...}`) unconditionally — an
    # .env copied verbatim from .env.example fails `up -d` before a single
    # container starts, and (verified above) an empty value is refused the
    # same way even with ROBOCO_CLOUD_AUTH_ENABLED=true set alongside it, so
    # there is no "leave it blank for cloud auth" option today. Mint it the
    # same way `make panel-token` does (roboco/agents_config.py
    # issue_panel_token: hex HMAC-SHA256 of "<ceo-id>:ceo:" keyed by the auth
    # secret) using only stdlib, so a fresh .env is actually runnable end to
    # end — the cloud-auth conflict is surfaced as a loud warning below
    # instead of a startup failure.
    if ! PANEL_TOKEN="$(python3 -c "
import hashlib, hmac, sys
secret = sys.argv[1].encode()
msg = sys.argv[2].encode()
print(hmac.new(secret, msg, hashlib.sha256).hexdigest())
" "$AGENT_AUTH_SECRET" "${CEO_AGENT_ID}:ceo:")"; then
        rm -f "$ENV_FILE"
        fail "Could not derive ROBOCO_PANEL_AGENT_TOKEN from the generated secret. ${ENV_FILE} removed — re-run once fixed."
    fi

    sed -i.bak "s/^ROBOCO_ENCRYPTION_KEY=$/ROBOCO_ENCRYPTION_KEY=${ENCRYPTION_KEY}/" "$ENV_FILE"
    sed -i.bak "s/^ROBOCO_AGENT_AUTH_SECRET=$/ROBOCO_AGENT_AUTH_SECRET=${AGENT_AUTH_SECRET}/" "$ENV_FILE"
    sed -i.bak "s/^ROBOCO_PANEL_AGENT_TOKEN=$/ROBOCO_PANEL_AGENT_TOKEN=${PANEL_TOKEN}/" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"

    log "Generated ROBOCO_ENCRYPTION_KEY, ROBOCO_AGENT_AUTH_SECRET, and ROBOCO_PANEL_AGENT_TOKEN into ${ENV_FILE}."
    log "Edit ${ENV_FILE} now if you want a pinned ROBOCO_VERSION, Grok, or other optional settings — quickstart won't touch it again."

    if [ "$CLOUD_AUTH_HINT" = "true" ]; then
        log "WARNING: ROBOCO_CLOUD_AUTH_ENABLED=true detected, but docker-compose.registry.yml's nginx service still hard-requires a non-empty ROBOCO_PANEL_AGENT_TOKEN just to start (an empty value is refused the same as unset, cloud auth or not — verified). quickstart minted one anyway so 'up -d' doesn't fail outright, but THIS TOKEN IS A STANDING CEO CREDENTIAL THAT BYPASSES YOUR LOGIN PAGE. Once cloud auth (TLS + creds) is fully live, blank ROBOCO_PANEL_AGENT_TOKEN= in ${ENV_FILE} and restart the stack."
    fi
    log "Note: ROBOCO_PANEL_AGENT_TOKEN is a standing CEO credential — blank it if you later arm cloud auth (see ${ENV_EXAMPLE})."
fi

# --- Stage 2: pull + up ------------------------------------------------------

log "Pulling images (docker compose -f ${COMPOSE_FILE} pull)..."
docker compose -f "$COMPOSE_FILE" pull \
    || fail "'docker compose -f ${COMPOSE_FILE} pull' failed. Check network access to ghcr.io/rennf93 and docker.io/renzof93, or that ROBOCO_REGISTRY/ROBOCO_VERSION in ${ENV_FILE} name a reachable registry/tag."

log "Starting the stack (docker compose -f ${COMPOSE_FILE} up -d)..."
docker compose -f "$COMPOSE_FILE" up -d \
    || fail "'docker compose -f ${COMPOSE_FILE} up -d' failed. Run 'docker compose -f ${COMPOSE_FILE} logs' for details."

# --- Stage 3: doctor-style readiness sweep ----------------------------------

DEADLINE=$(($(date +%s) + TIMEOUT_SECONDS))
CORE_SERVICES="postgres redis ollama orchestrator panel nginx"

check_services_running() {
    local running
    running="$(docker compose -f "$COMPOSE_FILE" ps --status running --services 2>/dev/null)" || return 1
    local svc
    for svc in $CORE_SERVICES; do
        echo "$running" | grep -qx "$svc" || return 1
    done
}

check_health() {
    curl -sf -o /dev/null "${BASE_URL}/health"
}

check_api_routing() {
    curl -sf -o /dev/null "${BASE_URL}/api/auth/status"
}

check_migrations() {
    docker compose -f "$COMPOSE_FILE" logs orchestrator 2>/dev/null | grep -q "Alembic upgrade finished"
}

check_ollama_models() {
    local models
    models="$(docker compose -f "$COMPOSE_FILE" exec -T ollama ollama list 2>/dev/null)" || return 1
    echo "$models" | grep -q "qwen3-embedding" && echo "$models" | grep -q "glm-5.2"
}

log "Waiting for the stack to become ready (timeout ${TIMEOUT_SECONDS}s)..."

DOCTOR_LINES=()

if retry_until "$DEADLINE" check_services_running; then
    DOCTOR_LINES+=("[ok] compose services up: ${CORE_SERVICES}")
else
    fail "Timed out waiting for core services to reach 'running' (${CORE_SERVICES}). Run 'docker compose -f ${COMPOSE_FILE} ps' and 'docker compose -f ${COMPOSE_FILE} logs' to see which one is stuck."
fi

if retry_until "$DEADLINE" check_health; then
    DOCTOR_LINES+=("[ok] health endpoint: GET ${BASE_URL}/health")
else
    fail "Timed out waiting for GET ${BASE_URL}/health (nginx's direct, unauthenticated proxy to the orchestrator — see docker/nginx.conf; NOT /api/health, which 404s since roboco/api/routes/health.py mounts with no /api prefix). Run 'docker compose -f ${COMPOSE_FILE} logs orchestrator nginx'."
fi

if retry_until "$DEADLINE" check_api_routing; then
    DOCTOR_LINES+=("[ok] API routing: GET ${BASE_URL}/api/auth/status")
else
    fail "Timed out waiting for GET ${BASE_URL}/api/auth/status (always-mounted, unauthenticated probe — roboco/api/auth/routes.py). /health passed but the nginx -> /api/ -> orchestrator path isn't answering; run 'docker compose -f ${COMPOSE_FILE} logs nginx orchestrator'."
fi

if retry_until "$DEADLINE" check_migrations; then
    DOCTOR_LINES+=("[ok] DB migrations at head (orchestrator log: \"Alembic upgrade finished\")")
else
    fail "Timed out waiting for the orchestrator log to report \"Alembic upgrade finished\" (roboco/db/base.py run_migrations()). Run 'docker compose -f ${COMPOSE_FILE} logs orchestrator | grep -i alembic'."
fi

if retry_until "$DEADLINE" check_ollama_models; then
    DOCTOR_LINES+=("[ok] Ollama models present: qwen3-embedding, glm-5.2")
else
    fail "Timed out waiting for Ollama to report both qwen3-embedding and glm-5.2 (docker compose exec ollama ollama list). Run 'docker compose -f ${COMPOSE_FILE} logs ollama-init' — a fresh pull can take a couple of minutes."
fi

echo ""
echo "=== RoboCo doctor summary ==="
for line in "${DOCTOR_LINES[@]}"; do
    echo "  ${line}"
done
echo ""
log "RoboCo is up: ${BASE_URL}"
