#!/bin/bash
# SessionStart hook — starts the SDK server and prints the pre-rendered
# task briefing into the session so Claude doesn't burn its first turns
# on tool discovery (roboco_task_scan → roboco_task_get → read files).
#
# The briefing is written by the orchestrator before the container spawns
# (_write_agent_briefing) and mounted read-only at /app/briefing.md.

SDK_PORT="${ROBOCO_SDK_PORT:-9000}"
AGENT_ID="${ROBOCO_AGENT_ID:-unknown}"
LOG_FILE="/tmp/sdk-server.log"
BRIEFING_FILE="/app/briefing.md"
PRECOMPACT_FILE="/tmp/roboco-precompact-${AGENT_ID}.md"

# --- SDK bring-up ---------------------------------------------------------
if ! curl -sf "http://localhost:${SDK_PORT}/health" >/dev/null 2>&1; then
    echo "[SDK] Starting for agent ${AGENT_ID} on port ${SDK_PORT}..."
    nohup uv run python -m roboco.agent_sdk.server > "$LOG_FILE" 2>&1 &
    SDK_PID=$!
    sleep 2
    if curl -sf "http://localhost:${SDK_PORT}/health" >/dev/null 2>&1; then
        echo "[SDK] Ready (PID: ${SDK_PID})"
    else
        echo "[SDK] Starting in background (PID: ${SDK_PID}, check ${LOG_FILE})"
    fi
fi

# Reset budget/terminal counters at the start of every session.
curl -sf -m 2 -X POST "http://localhost:${SDK_PORT}/budget/reset" >/dev/null 2>&1 || true

# --- Briefing + PreCompact recovery --------------------------------------
# Compact restore comes FIRST so it's clear what this session is resuming.
if [[ -s "$PRECOMPACT_FILE" ]]; then
    echo "### Resumed from compact"
    cat "$PRECOMPACT_FILE"
    echo
fi

if [[ -s "$BRIEFING_FILE" ]]; then
    cat "$BRIEFING_FILE"
fi

exit 0
