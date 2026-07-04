#!/usr/bin/env bash
# Fable honesty rule: when Bash output shows failures, nudge verbatim
# reporting. Ported from opus-fable-playbook hooks/honesty-nudge.sh (v0.1.3)
# — see docs/superpowers/plans/2026-07-04-v0.18.0-A-opus-fable-plan.md.
# PostToolUse[Bash], non-blocking (context injection only, never denies) —
# the one hook shipped to BOTH the Claude settings.json path (snake_case
# tool_response) and the grok hooks path (camelCase toolResponse candidate,
# unconfirmed by live spike; parsed defensively per bash-guard-hook.sh's
# existing tool_input/toolInput precedent — see Task 9/10 notes in the plan).
# Fail-open: any internal error => exit 0.
set -u

INPUT="$(cat)" || exit 0
RESP="$(printf '%s' "$INPUT" | python3 -c \
  'import json,sys
d = json.load(sys.stdin)
print(json.dumps(d.get("tool_response") or d.get("toolResponse") or ""))' \
  2>/dev/null || true)"
[ -z "$RESP" ] || [ "$RESP" = '""' ] && exit 0

HIT=0
printf '%s' "$RESP" | grep -qE 'FAILED |= FAILURES =|test result: FAILED|--- FAIL|AssertionError|Traceback \(most recent call last\)' && HIT=1
printf '%s' "$RESP" | grep -qE 'Tests:[^"]*failed' && HIT=1
[ "$HIT" -eq 0 ] && exit 0

cat <<'JSON'
{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "A command just reported failures. Fable honesty rule: report this outcome verbatim (the actual failing output) in your final message; do not summarize it as mostly-working or claim success."}}
JSON
exit 0
