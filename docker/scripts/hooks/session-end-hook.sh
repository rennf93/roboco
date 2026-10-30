#!/usr/bin/env bash
# SessionEnd hook — post-mortem to the journal.
#
# When Claude Code exits for any reason, we ask the SDK to write a
# reflective journal entry summarising the session: total tool calls,
# whether the budget halted, whether a loop was detected, and which
# terminal tool (if any) was the last action. PMs read journals when
# reviewing cell work so this data surfaces without a separate dashboard.

set -u

SDK_URL="${ROBOCO_SDK_URL:-http://localhost:9000}"

budget=$(curl -sf -m 2 "$SDK_URL/budget/status" 2>/dev/null || echo "")
terminal=$(curl -sf -m 2 "$SDK_URL/terminal/status" 2>/dev/null || echo "")

if [[ -z "$budget" && -z "$terminal" ]]; then
    # SDK unreachable — nothing to post. Let the container exit clean.
    exit 0
fi

total=0
halted=false
looped=false
last_tool="null"

if [[ -n "$budget" ]]; then
    total=$(echo "$budget" | jq -r '.total // 0')
    halted=$(echo "$budget" | jq -r '.halt // false')
    looped=$(echo "$budget" | jq -r '.loop // false')
fi
if [[ -n "$terminal" ]]; then
    last_tool=$(echo "$terminal" | jq -r '.last_tool // "null"')
fi

payload=$(jq -nc \
    --arg terminal_tool "$last_tool" \
    --argjson tools_called "$total" \
    --argjson loop_triggered "$looped" \
    --argjson halt_triggered "$halted" \
    --arg reason "session_end" \
    '{terminal_tool: $terminal_tool, tools_called: $tools_called, loop_triggered: $loop_triggered, halt_triggered: $halt_triggered, reason: $reason}')

curl -sf -m 3 -X POST "$SDK_URL/journal/post_mortem" \
    -H "Content-Type: application/json" \
    -d "$payload" >/dev/null 2>&1 || true

exit 0
