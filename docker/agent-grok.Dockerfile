# Grok (xAI) Agent Image
# =============================================================================
# Runs grok-build-0.1 through the opencode CLI (OpenAI protocol) instead of
# Claude Code, while reusing the base image's roboco venv + uv + the RoboCo MCP
# gateway servers. The entrypoint renders opencode.json from the spawn env +
# mounted mcp-config.json (see roboco.llm.providers.opencode_config) and runs
# opencode. One runtime image serves every role — role behaviour comes from the
# mounted system prompt / manifest / mcp-config, exactly as on the Claude path.
# =============================================================================

FROM roboco-agent-base

USER root

# opencode — the OpenAI-protocol agent runtime. grok-build-0.1 is driven via the
# OpenAI Responses API, so the provider package is @ai-sdk/openai (NOT
# @ai-sdk/openai-compatible, which is chat/completions only and errors with
# "responses is not a function"). opencode resolves it at runtime, but
# pre-installing keeps first spawn off the network.
RUN npm install -g opencode-ai @ai-sdk/openai \
    && npm cache clean --force \
    && rm -rf /root/.npm /tmp/*

# opencode plugins (referenced from the generated opencode.json `plugin:` array):
#   secret-scrub — bash-guard parity (PAT/credential deny on tool.execute.before)
#   budget-feed  — POSTs budget/loop/terminal counters to the in-container SDK
#                  server (tool.execute.{before,after}); the entrypoint starts
#                  that server (roboco.agent_sdk.server) for Claude-parity.
COPY docker/grok/secret-scrub.js /app/opencode-plugins/secret-scrub.js
COPY docker/grok/budget-feed.js /app/opencode-plugins/budget-feed.js

# Entrypoint: render opencode.json, then run opencode (overrides base's `claude`).
COPY docker/scripts/grok-agent-entrypoint.sh /app/scripts/grok-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/grok-agent-entrypoint.sh

# opencode persists data under ~/.local/share and state under ~/.local/state.
# When the orchestrator bind-mounts the opencode store at
# ~/.local/share/opencode, docker creates the intermediate ~/.local AS ROOT, so
# the non-root agent can no longer create its sibling ~/.local/state and opencode
# EACCESes at boot. Pre-create the tree agent-owned so the mount leaves the
# parents writable (complements the orchestrator's 0777 host-source pre-create).
RUN mkdir -p /home/agent/.local/share/opencode /home/agent/.local/state \
    && chown -R agent:agent /home/agent/.local

USER agent

LABEL role="grok-runtime"
LABEL description="Grok (xAI) agent runtime — grok-build-0.1 via opencode (OpenAI protocol)"

ENTRYPOINT ["/app/scripts/grok-agent-entrypoint.sh"]
