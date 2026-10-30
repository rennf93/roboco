#!/bin/bash
# HUMMIN live-chat entrypoint (roboco-agent-hummin-live): render the hummin
# settings (auth + SYSTEM.md role blueprint) AND the pi tool extension (the
# Secretary/Intake bridge - hummin has no MCP client), then exec the
# provider-generic interactive driver. Auth = ZAI_API_KEY env (passed at
# spawn), same as the one-shot path.
set -euo pipefail
cd /app
python -m roboco.llm.providers.hummin_cli_config
python -m roboco.llm.providers.hummin_live_config
exec python -m roboco.agent_sdk.live_main
