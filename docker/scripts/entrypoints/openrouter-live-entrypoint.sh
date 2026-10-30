#!/bin/bash
# OPENROUTER live-chat entrypoint (roboco-agent-openrouter-live): render the
# opencode config (role blueprint + tool scoping from the mounted live
# mcp-config), then exec the provider-generic interactive driver. Auth =
# OPENROUTER_API_KEY env (passed at spawn), same as the one-shot path.
set -euo pipefail
cd /app
python -m roboco.llm.providers.openrouter_cli_config
exec python -m roboco.agent_sdk.live_main
