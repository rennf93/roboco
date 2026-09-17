#!/bin/bash
# GEMINI live-chat entrypoint (roboco-agent-gemini-live): render the gemini
# config (role blueprint + tool scoping from the mounted live mcp-config),
# then exec the provider-generic interactive driver. Auth = the ~/.gemini
# mount (OAuth), same as the one-shot path.
set -euo pipefail
cd /app
python -m roboco.llm.providers.gemini_cli_config
exec python -m roboco.agent_sdk.live_main
