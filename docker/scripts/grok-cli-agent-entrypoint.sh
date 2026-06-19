#!/bin/bash
# Grok CLI agent entrypoint wrapper.
#
# Responsibilities for full runtime hooks parity:
# - Start the SDK server (and emit briefing) via sdk-startup-hook.sh BEFORE grok -p.
#   This satisfies SessionStart equivalent + A2A/budget/usage counters.
# - Grok discovers and activates the other hooks (PreToolUse, PostToolUse, Stop,
#   UserPromptSubmit, PreCompact, SessionEnd) from the mounted
#   /home/agent/.grok/user-settings.json (populated by orchestrator with identical
#   command registrations as Claude).
# - Exec the real `grok` CLI in headless mode with streaming-json, MCP, prompt.
#
# The scripts are CLI-agnostic (read JSON events from stdin, POST to localhost:9000).
# This entrypoint + generated user-settings gives the "full set".
#
# Orchestrator sets ROBOCO_INITIAL_PROMPT (and CLAUDE_CODE_SUBAGENT_MODEL etc)
# via -e; entrypoint builds the clean grok argv and execs (no forwarded flags).
set -euo pipefail

# Match the robust uv env pinning used by Claude hooks + mcp-config.
export UV_PROJECT_ENVIRONMENT=/app/.venv
export PYTHONUNBUFFERED=1

AGENT_ID="${ROBOCO_AGENT_ID:-unknown}"
echo "[grok-entrypoint] agent=${AGENT_ID} starting full instrumentation..."

# 1. SDK startup + briefing (SessionStart hook equivalent). Idempotent.
if [ -x /app/scripts/sdk-startup-hook.sh ]; then
    # Run in subshell so set -e doesn't kill us on non-fatal inside hook
    (/app/scripts/sdk-startup-hook.sh || echo "[grok-entrypoint] sdk-startup-hook completed with notes") 2>&1 | cat
else
    echo "[grok-entrypoint] WARNING: sdk-startup-hook.sh missing"
fi

# 2. Ensure grok can find its user-settings (hooks) and home.
mkdir -p /home/agent/.grok /tmp/grok-logs
# (the settings file is bind-mounted ro by orchestrator when provider=xai)

# 3. Reconstruct the prompt. Prefer env injected by orchestrator; fall back.
INITIAL_PROMPT="${ROBOCO_INITIAL_PROMPT:-${PROMPT:-}}"
if [ -z "${INITIAL_PROMPT}" ]; then
    # Last resort: if orchestrator appended literal prompt tokens, use them.
    # In practice orchestrator _launch_spawn/_append sets the prompt via env for wrappers.
    INITIAL_PROMPT="Follow the system prompt in /app/system-prompt.md and briefing if present. Begin task work."
fi

# 4. Build grok invocation. Grok Build supports:
#   grok -p "..." --output-format streaming-json --verbose -m model
#   --mcp-config , instructions etc (best effort; grok will ignore unknown safely).
GROK_CMD=(grok -p "${INITIAL_PROMPT}" --output-format streaming-json --verbose)

# Model (orchestrator puts resolved in CLAUDE_CODE_SUBAGENT_MODEL or we use config)
if [ -n "${CLAUDE_CODE_SUBAGENT_MODEL:-}" ]; then
    GROK_CMD+=(-m "${CLAUDE_CODE_SUBAGENT_MODEL}")
elif [ -n "${ROBOCO_AGENT_MODEL:-}" ]; then
    GROK_CMD+=(-m "${ROBOCO_AGENT_MODEL}")
fi

# System prompt / instructions
if [ -f /app/system-prompt.md ]; then
    # Grok supports --instructions or reads project config; pass as text.
    SYS_PROMPT="$(cat /app/system-prompt.md | head -c 12000 || true)"
    if [ -n "$SYS_PROMPT" ]; then
        GROK_CMD+=(--instructions "$SYS_PROMPT")
    fi
fi

# MCP config (for flow/do/optimal/git). Grok Build supports MCP servers.
if [ -f /app/mcp-config.json ]; then
    GROK_CMD+=(--mcp-config /app/mcp-config.json || true)
fi

# 5. Exec grok (replaces shell; grok will source hooks from user-settings.json).
echo "[grok-entrypoint] exec: ${GROK_CMD[*]}"
exec "${GROK_CMD[@]}"
