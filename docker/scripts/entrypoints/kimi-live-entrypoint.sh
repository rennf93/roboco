#!/bin/bash
# KIMI live-chat entrypoint (roboco-agent-kimi-live): render the kimi config
# (role blueprint + tool scoping from the mounted live mcp-config), then exec
# the provider-generic interactive driver. Auth = the ~/.kimi-code mount.
set -euo pipefail
# Group-writable output: the orchestrator's workspace git user shares the
# agent group and must be able to rewrite what this agent creates.
umask 002
cd /app
python -m roboco.llm.providers.kimi_cli_config
exec python -m roboco.agent_sdk.live_main
