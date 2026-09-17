#!/bin/bash
# CODEX live-chat entrypoint (roboco-agent-codex-live): render the codex
# config (role blueprint + tool scoping from the mounted live mcp-config),
# then exec the provider-generic interactive driver. Auth = the ~/.codex
# mount (subscription OAuth), same as the one-shot path.
set -euo pipefail
cd /app
python -m roboco.llm.providers.codex_cli_config
exec python -m roboco.agent_sdk.live_main
