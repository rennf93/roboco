#!/usr/bin/env bash
# Entrypoint for the roboco-agent-grok image.
#
# Renders opencode.json from the RoboCo spawn env (OPENAI_* + ROBOCO_*, set by
# GrokProvider) plus the mounted Claude Code mcp-config.json, then runs opencode
# non-interactively. opencode speaks the OpenAI protocol, so grok-build-0.1 runs
# natively against api.x.ai/v1 with no shim, while still reaching the RoboCo MCP
# gateway (roboco-flow / roboco-do / ...) translated into opencode's mcp config.
set -euo pipefail

# Generate opencode.json (provider + model + MCP gateway + permissions +
# instructions). Writes to opencode's global config dir by default.
python -m roboco.llm.providers.opencode_config

# Run the agent. The prompt comes from an env var (never an untrusted argv
# positional); `--` separates it from flags so a prompt starting with `--`
# cannot be parsed as CLI options. The model also comes from the rendered
# config; --model is passed explicitly as belt-and-suspenders.
#
# `< /dev/null` is REQUIRED: without a closed stdin, `opencode run` hangs after
# init in a headless / no-TTY environment (it blocks waiting on stdin). Verified
# live — closing stdin lets the run proceed to the model call and exit cleanly.
#
# Reasoning effort: GrokProvider sets ROBOCO_GROK_VARIANT per role (e.g.
# "minimal" for coordination/docs roles to cut reasoning cost). Absent =
# opencode default (full reasoning).
variant_arg=()
if [ -n "${ROBOCO_GROK_VARIANT:-}" ]; then
  variant_arg=(--variant "$ROBOCO_GROK_VARIANT")
fi

# Prompt-injection guard (parity with the Claude UserPromptSubmit hook): the
# task prompt is DATA, not instructions — refuse a poisoned one before it
# reaches the model. Same patterns as docker/scripts/user-prompt-hook.sh.
if ! python -m roboco.agent_sdk.prompt_guard "${ROBOCO_INITIAL_PROMPT:-}"; then
  echo "Refusing to run: task prompt matched a prompt-injection pattern." >&2
  exit 1
fi

exec opencode run \
  --model "xai/${ROBOCO_AGENT_MODEL:-grok-build-0.1}" \
  "${variant_arg[@]}" \
  -- "${ROBOCO_INITIAL_PROMPT:-}" < /dev/null
