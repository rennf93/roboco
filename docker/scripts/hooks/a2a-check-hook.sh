#!/bin/bash
# A2A Check Hook
#
# Claude Code hook that runs after each tool call to check for
# incoming A2A messages. Notifies Claude if messages are pending.
#
# This hook is non-blocking and always succeeds to avoid
# interrupting Claude's workflow.

SDK_URL="${ROBOCO_SDK_URL:-http://localhost:9000}"

# Check inbox count (non-consuming endpoint)
response=$(curl -sf "$SDK_URL/inbox/count" 2>/dev/null)

if [ $? -eq 0 ]; then
    total=$(echo "$response" | jq -r '.total // 0')
    urgent=$(echo "$response" | jq -r '.urgent // 0')

    if [ "$total" -gt 0 ]; then
        if [ "$urgent" -gt 0 ]; then
            echo "[A2A] URGENT: You have $urgent urgent message(s). Use roboco_a2a_check() to read them."
        else
            echo "[A2A] You have $total pending message(s). Use roboco_a2a_check() to read them."
        fi
    fi
fi

# Always exit 0 - don't block Claude
exit 0
