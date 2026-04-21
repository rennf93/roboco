#!/usr/bin/env bash
# PreCompact hook — snapshot state before Claude Code compacts history.
#
# After a compact, recent tool-call history is lost. We snapshot budget,
# terminal, and last-tool state to a file that sdk-startup-hook.sh re-emits
# into the next session so Claude re-enters with continuity.

set -u

SDK_URL="${ROBOCO_SDK_URL:-http://localhost:9000}"
AGENT_ID="${ROBOCO_AGENT_ID:-unknown}"
OUT="/tmp/roboco-precompact-${AGENT_ID}.md"

budget=$(curl -sf -m 2 "$SDK_URL/budget/status" 2>/dev/null || echo "")
terminal=$(curl -sf -m 2 "$SDK_URL/terminal/status" 2>/dev/null || echo "")

if [[ -z "$budget" && -z "$terminal" ]]; then
    # Nothing to snapshot — SDK unreachable. Don't stall the compact.
    exit 0
fi

{
    echo "## Pre-compact snapshot"
    echo
    echo "_Written by PreCompact hook before Claude Code compacted this session._"
    echo
    if [[ -n "$budget" ]]; then
        total=$(echo "$budget" | jq -r '.total // 0')
        halt=$(echo "$budget" | jq -r '.halt_threshold // 150')
        warn=$(echo "$budget" | jq -r '.warn // false')
        looped=$(echo "$budget" | jq -r '.loop // false')
        echo "- **Tool calls:** ${total}/${halt} (warn=${warn}, loop=${looped})"
    fi
    if [[ -n "$terminal" ]]; then
        last=$(echo "$terminal" | jq -r '.last_tool // "null"')
        had_term=$(echo "$terminal" | jq -r '.had_terminal_recently // false')
        recent=$(echo "$terminal" | jq -r '.recent_tools // [] | join(" → ")')
        echo "- **Last tool:** \`${last}\`"
        echo "- **Recent window:** ${recent}"
        echo "- **Terminal tool in window:** ${had_term}"
    fi
    echo
    echo "Pick up where you left off — do NOT re-fetch the task if you already"
    echo "know it; the briefing below restates your current assignment."
} > "$OUT" 2>/dev/null || true

exit 0
