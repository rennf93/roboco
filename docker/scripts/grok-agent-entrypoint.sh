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
exec opencode run \
  --model "xai/${ROBOCO_AGENT_MODEL:-grok-build-0.1}" \
  -- "${ROBOCO_INITIAL_PROMPT:-}"
