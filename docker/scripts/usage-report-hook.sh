#!/usr/bin/env bash
# PostToolUse + Stop: report or sync token usage.
#
# Grok Build (xAI provider path) supplies token deltas directly in the hook
# payload (tokens_input, tokens_output, ...). We POST those additively to
# /usage/report so the SDK accumulates real counts for Grok sessions.
#
# Claude Code does not pass token counts to hooks, but passes transcript_path.
# We fall back to POST /usage/sync (absolute set from JSONL) — idempotent and
# safe to call repeatedly. The orchestrator reads /usage/status to finalize
# sessions and feed the dashboard.
#
# Fire-and-forget — never block the agent. Always exit 0.

set -u

SDK_URL="${ROBOCO_SDK_URL:-http://localhost:9000}"
input=$(cat 2>/dev/null || true)
[[ -z "$input" ]] && exit 0

# Dual-path extraction:
# - If deltas present (Grok): prefer /usage/report (additive).
# - Else fall back to transcript_path (Claude) -> /usage/sync (absolute).
mode_data=$(printf '%s' "$input" | python3 - <<'PY'
import json, sys
try:
    d = json.loads(sys.stdin.read()) or {}
except Exception:
    d = {}

# Search top level, tool_input, and a possible "usage" wrapper for deltas.
cands = [d]
if isinstance(d, dict):
    ti = d.get("tool_input") or {}
    if isinstance(ti, dict):
        cands.append(ti)
    u = d.get("usage") or {}
    if isinstance(u, dict):
        cands.append(u)

tokens = {}
for cand in cands:
    if not isinstance(cand, dict):
        continue
    for k in ("tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_write"):
        if k in cand:
            try:
                tokens[k] = int(cand.get(k) or 0)
            except (TypeError, ValueError):
                tokens[k] = 0

if tokens:
    print("REPORT:" + json.dumps(tokens))
else:
    tp = d.get("transcript_path", "") if isinstance(d, dict) else ""
    print("SYNC:" + (tp or ""))
PY
)

if [[ "$mode_data" == REPORT:* ]]; then
    payload="${mode_data#REPORT:}"
    [[ -z "$payload" || "$payload" == "{}" ]] && exit 0
    curl -sf -m 3 -X POST "$SDK_URL/usage/report" \
        -H "Content-Type: application/json" \
        -d "$payload" >/dev/null 2>&1 || true
elif [[ "$mode_data" == SYNC:* ]]; then
    TRANSCRIPT="${mode_data#SYNC:}"
    [[ -z "$TRANSCRIPT" ]] && exit 0
    curl -sf -m 3 -X POST "$SDK_URL/usage/sync" \
        -H "Content-Type: application/json" \
        -d "{\"transcript_path\":\"$TRANSCRIPT\"}" >/dev/null 2>&1 || true
fi

exit 0
