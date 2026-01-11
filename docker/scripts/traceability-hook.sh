#!/bin/bash
# Traceability Hook
#
# Claude Code hook that runs after key tool calls to provide
# context-aware reminders for documentation and traceability.
#
# Covers: journaling, task notes, progress updates, KB search,
# communication, verification checks.
#
# This hook is non-blocking and always succeeds to avoid
# interrupting Claude's workflow.

SDK_URL="${ROBOCO_SDK_URL:-http://localhost:9000}"
TOOL_NAME="${CLAUDE_TOOL_NAME:-unknown}"

# Call SDK with tool name for context-aware suggestion
response=$(curl -sf "$SDK_URL/traceability/remind?tool=$TOOL_NAME" 2>/dev/null)

if [ $? -eq 0 ]; then
    should_remind=$(echo "$response" | jq -r '.should_remind // false')
    if [ "$should_remind" = "true" ]; then
        reminder_type=$(echo "$response" | jq -r '.type // "general"')
        suggestion=$(echo "$response" | jq -r '.suggestion // ""')

        # Different prefixes for 6 reminder types
        case "$reminder_type" in
            "verify")
                echo "[Check] $suggestion"
                ;;
            "reflect")
                echo "[Reflect] $suggestion"
                ;;
            "struggle")
                echo "[Document] $suggestion"
                ;;
            "message")
                echo "[Communicate] $suggestion"
                ;;
            "kb")
                echo "[Research] $suggestion"
                ;;
            "journal"|*)
                echo "[Trace] $suggestion"
                ;;
        esac
    fi
fi

# Always exit 0 - don't block Claude
exit 0
