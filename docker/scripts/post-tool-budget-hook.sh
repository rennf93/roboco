#!/usr/bin/env bash
# PostToolUse: per-session budget counter + loop detector.
#
# Runs after every tool call. Posts a (tool, args_hash) pair to the SDK
# server, which tracks cumulative counts and a rolling window of identical
# calls. Emits a short reminder line to stdout when thresholds are hit so
# Claude sees it in the next turn:
#
#   [Budget]  — soft warning (past warn threshold)
#   [Loop]    — same tool+args ≥ loop_threshold times in the window
#   [Halt]    — hard cap breached; orchestrator kill-switch will terminate
#               the container on its next sweep. Hook also fires the
#               auto-escalate on the agent's behalf.
#
# Non-blocking: exit 0 always (this is a reminder, not a guard).

set -u

SDK_URL="${ROBOCO_SDK_URL:-http://localhost:9000}"
input=$(cat 2>/dev/null || true)
[[ -z "$input" ]] && exit 0

# Strip MCP prefix, keep tool_input deterministic for hash.
read -r TOOL ARGS_HASH <<<"$(printf '%s' "$input" | python3 - <<'PY'
import json, sys, hashlib
try:
    d = json.loads(sys.stdin.read())
    tool = d.get("tool_name", "")
    ti = d.get("tool_input") or {}
    blob = json.dumps(ti, sort_keys=True, separators=(",", ":"), default=str)
    h = hashlib.sha256(blob.encode("utf-8", errors="ignore")).hexdigest()[:16]
    print(f"{tool} {h}")
except Exception:
    print("unknown unknown")
PY
)"

# Ask the SDK to record + return status. 2s timeout — we never block Claude.
resp=$(curl -sf -m 2 -X POST "$SDK_URL/budget/tool_called" \
    -H "Content-Type: application/json" \
    -d "{\"tool\":\"$TOOL\",\"args_hash\":\"$ARGS_HASH\"}" 2>/dev/null)

[[ -z "$resp" ]] && exit 0

total=$(echo "$resp" | jq -r '.total // 0')
warn=$(echo "$resp" | jq -r '.warn // false')
halt=$(echo "$resp" | jq -r '.halt // false')
loop=$(echo "$resp" | jq -r '.loop // false')
halt_threshold=$(echo "$resp" | jq -r '.halt_threshold // 150')

if [[ "$halt" == "true" ]]; then
    echo "[Halt] Budget exceeded: ${total}/${halt_threshold} tool calls. Auto-escalating; stop now."
    # Fire-and-forget the substitute so the task gets released even if the
    # agent ignores the message. Orchestrator sweep will terminate the
    # container within agent_budget_sweep_interval_seconds anyway.
    curl -sf -m 2 -X POST "$SDK_URL/terminal/force_substitute" >/dev/null 2>&1 || true
elif [[ "$loop" == "true" ]]; then
    echo "[Loop] Same tool+args repeated in window. Stop looping — escalate via roboco_task_escalate() or substitute."
elif [[ "$warn" == "true" ]]; then
    echo "[Budget] ${total}/${halt_threshold} tool calls used. Plan your remaining work carefully."
fi

exit 0
