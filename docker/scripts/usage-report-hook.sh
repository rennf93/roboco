#!/usr/bin/env bash
# PostToolUse + Stop: sync token usage from the Claude Code transcript.
#
# Claude Code does not pass token counts to hooks, but it does pass the
# path to the session transcript (.jsonl), which records per-message
# `usage`. We hand that path to the SDK server, which parses the transcript
# and SETS the cumulative totals (absolute, idempotent — safe to call after
# every tool and again at Stop). The orchestrator later reads these via
# /usage/status to finalize the spawn-session row and the usage dashboard.
#
# Fire-and-forget — never block Claude on this. Always exit 0.

set -u

SDK_URL="${ROBOCO_SDK_URL:-http://localhost:9000}"
input=$(cat 2>/dev/null || true)
[[ -z "$input" ]] && exit 0

TRANSCRIPT=$(printf '%s' "$input" | python3 -c "$(cat <<'PY'
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get("transcript_path", ""))
except Exception:
    print("")
PY
)")

[[ -z "$TRANSCRIPT" ]] && exit 0

curl -sf -m 3 -X POST "$SDK_URL/usage/sync" \
    -H "Content-Type: application/json" \
    -d "{\"transcript_path\":\"$TRANSCRIPT\"}" >/dev/null 2>&1 || true

exit 0
