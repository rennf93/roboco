#!/usr/bin/env bash
# Smoke: bash-guard denial message is <= 3 lines (was 8+).
# Smoke run 3 (2026-05-12) showed agents getting an 8-line refusal on
# every blocked shell-git op. Tighter messages save tokens on retries.
set -e

cd "$(dirname "$0")/../.."

HOOK=docker/scripts/bash-guard-hook.sh

# Count lines in the longest heredoc denial block (cat <<'EOF' ... EOF style).
# The first git-network denial used a heredoc; awk counts echo-or-content
# lines inside it.
HEREDOC_LINES=$(awk '
  /cat <<'"'"'EOF'"'"'/ { in_block = 1; count = 0; next }
  in_block && /^EOF$/ { print count; in_block = 0; count = 0; next }
  in_block { count++ }
  END { if (in_block && count > 0) print count }
' "$HOOK" | sort -n | tail -1)

# Also count contiguous echo-based denial blocks (other denial paths).
ECHO_LINES=$(awk '
  /Denied/ { in_block = 1; count = 0 }
  in_block && /^[[:space:]]*echo/ { count++ }
  in_block && /^[[:space:]]*$/ && count > 0 { print count; in_block = 0; count = 0 }
  END { if (in_block && count > 0) print count }
' "$HOOK" | sort -n | tail -1)

MAX_LINES=0
[[ -n "$HEREDOC_LINES" && "$HEREDOC_LINES" -gt "$MAX_LINES" ]] && MAX_LINES=$HEREDOC_LINES
[[ -n "$ECHO_LINES" && "$ECHO_LINES" -gt "$MAX_LINES" ]] && MAX_LINES=$ECHO_LINES

if [ "$MAX_LINES" -eq 0 ]; then
    echo "FAIL: could not find any denial message block in $HOOK"
    exit 1
fi

if [ "$MAX_LINES" -gt 3 ]; then
    echo "FAIL: bash-guard denial message has $MAX_LINES lines (max 3)"
    exit 1
fi

echo "PASS: denial message has $MAX_LINES lines"
