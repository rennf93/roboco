#!/bin/bash
# SDK Server Startup Hook
#
# Called by Claude Code on SessionStart to start the SDK server.
# Runs in background, Claude continues immediately.

SDK_PORT="${ROBOCO_SDK_PORT:-9000}"
AGENT_ID="${ROBOCO_AGENT_ID:-unknown}"
LOG_FILE="/tmp/sdk-server.log"

# Check if SDK is already running
if curl -sf "http://localhost:${SDK_PORT}/health" >/dev/null 2>&1; then
    echo "[SDK] Already running on port ${SDK_PORT}"
    exit 0
fi

# Start SDK server in background (nohup to survive hook completion)
echo "[SDK] Starting for agent ${AGENT_ID} on port ${SDK_PORT}..."
nohup uv run python -m roboco.agent_sdk.server > "$LOG_FILE" 2>&1 &
SDK_PID=$!

# Brief wait for startup (non-blocking - don't hold up Claude)
sleep 2

# Check if it started
if curl -sf "http://localhost:${SDK_PORT}/health" >/dev/null 2>&1; then
    echo "[SDK] Ready (PID: ${SDK_PID})"
else
    echo "[SDK] Starting in background (PID: ${SDK_PID}, check ${LOG_FILE} for status)"
fi

exit 0
