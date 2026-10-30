#!/bin/bash
# KIMI live-chat entrypoint (roboco-agent-kimi-live): render the kimi config
# (role blueprint + tool scoping from the mounted live mcp-config), then exec
# the provider-generic interactive driver. Auth = the ~/.kimi-code mount.
set -euo pipefail
cd /app
python -m roboco.llm.providers.kimi_cli_config
exec python -m roboco.agent_sdk.live_main
