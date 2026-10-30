#!/bin/bash
# NEBIUS live-chat entrypoint (roboco-agent-nebius-live): render the opencode
# config (role blueprint + tool scoping from the mounted live mcp-config),
# then exec the provider-generic interactive driver. Auth = NEBIUS_API_KEY
# env (passed at spawn), same as the one-shot path.
set -euo pipefail
cd /app
python -m roboco.llm.providers.nebius_cli_config
exec python -m roboco.agent_sdk.live_main
