#!/bin/bash
# Grok CLI agent entrypoint wrapper (one-shot delivery agents).
#
# Responsibilities for full runtime hooks parity:
# - Invoke grok_cli_config (writes config.toml, ~/.grok/AGENTS.md role blueprint,
#   per-role --disallow/--deny, and FULL set of hook JSONs via write_grok_hooks).
# - Grok discovers/activates all hooks (SessionStart/sdk-startup, PreToolUse
#   bash-guard + others, PostToolUse a2a/budget/usage, Stop, UserPromptSubmit,
#   PreCompact, SessionEnd) from ~/.grok/hooks/*.json and/or user-settings.json .
# - Exec grok -p headless (streaming-json) using MCP + role flags.
#
# grok_cli_config on container start is the source of hook JSONs (AC2).
# Scripts are CLI-agnostic (stdin JSON -> POST localhost:9000).
set -euo pipefail

export UV_PROJECT_ENVIRONMENT=/app/.venv
export PYTHONUNBUFFERED=1

AGENT_ID="${ROBOCO_AGENT_ID:-unknown}"
echo "[grok-entrypoint] agent=${AGENT_ID} starting full instrumentation via grok_cli_config..."

# 1. Write config.toml + AGENTS.md (role blueprint) + FULL hooks JSONs + args.
# This satisfies "Hook JSONs written by grok_cli_config on container start".
# Run from /app so python -m finds installed package.
(cd /app && python -m roboco.llm.providers.grok_cli_config) || echo "[grok-entrypoint] grok_cli_config completed (non-fatal notes ok)"

mkdir -p /home/agent/.grok /tmp/grok-logs

# 2. Reconstruct prompt (orchestrator injects via ROBOCO_INITIAL_PROMPT).
INITIAL_PROMPT="${ROBOCO_INITIAL_PROMPT:-${PROMPT:-}}"
if [ -z "${INITIAL_PROMPT}" ]; then
    INITIAL_PROMPT="Follow the system prompt in /app/system-prompt.md and briefing if present. Begin task work."
fi

# Load any per-role args rendered by grok_cli_config (includes --always-approve,
# --disallowed-tools, --deny rules, --max-turns, --disable-web-search).
GROK_ARGS_FILE="${ROBOCO_GROK_ARGS_FILE:-/tmp/roboco-grok-args}"
GROK_ARGS=()
if [ -f "$GROK_ARGS_FILE" ]; then
    mapfile -t GROK_ARGS < "$GROK_ARGS_FILE"
fi

# 3. Exec grok. Hooks (including SessionStart for sdk) are active from the
# files written above. Use --cwd for workspace.
WORKSPACE="${ROBOCO_WORKSPACE:-$PWD}"
RUN_LOG="/tmp/grok-run.json"
ERR_LOG="/tmp/grok-run.err"

set +e
grok -p "${INITIAL_PROMPT}" \
    -m "${CLAUDE_CODE_SUBAGENT_MODEL:-${ROBOCO_AGENT_MODEL:-grok-build}}" \
    --cwd "$WORKSPACE" \
    --output-format json \
    "${GROK_ARGS[@]}" \
    < /dev/null > "$RUN_LOG" 2> "$ERR_LOG"
run_rc=$?
set -e

cat "$RUN_LOG"
[ -s "$ERR_LOG" ] && cat "$ERR_LOG" >&2

# Rate limit exit 75 for orchestrator parking (parity with canonical).
if grep -qiE '(\b429\b|rate.?limit|too many requests|quota|insufficient_quota)' \
    "$RUN_LOG" "$ERR_LOG" 2>/dev/null; then
    echo "[grok-entrypoint] rate-limited — exit 75"
    exit 75
fi

exit "$run_rc"
