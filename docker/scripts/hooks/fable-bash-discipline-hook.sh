#!/usr/bin/env bash
# Fable tool discipline: deny pure shell file-reads; dedicated tools exist.
# Ported from opus-fable-playbook hooks/bash-discipline.sh (v0.1.3) — see
# docs/superpowers/plans/2026-07-04-v0.18.0-A-opus-fable-plan.md.
# PreToolUse[Bash], Claude-only (see the plan's grok risk section — a grok
# hook deny cancels the whole run, so this is not shipped to grok in V1).
# Fail-open: any internal error => exit 0 (allow).
set -u

INPUT="$(cat)" || exit 0
CMD="$(printf '%s' "$INPUT" | python3 -c \
  'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' \
  2>/dev/null || true)"
[ -z "$CMD" ] && exit 0

# Pipelines, compounds, redirects, heredocs are legitimate — allow.
printf '%s' "$CMD" | grep -qE '\||&&|;|>|<<' && exit 0

DENY=0
printf '%s' "$CMD" | grep -qE '^[[:space:]]*(cat|head|tail|less|more)[[:space:]]' && DENY=1
printf '%s' "$CMD" | grep -qE '^[[:space:]]*sed[[:space:]]+-n[[:space:]]' && DENY=1
[ "$DENY" -eq 0 ] && exit 0

cat <<'JSON'
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "Fable tool discipline: use the dedicated Read/Grep tools instead of shell file-reads (cat/head/tail/less/sed -n). Read is paginated and line-numbered; Grep searches without loading whole files."}}
JSON
exit 0
