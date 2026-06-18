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

# Command guard / secret-scrub plugin (bash-guard parity for the opencode runtime).
# Referenced from the generated opencode.json `plugin:` array.
COPY docker/grok/secret-scrub.js /app/opencode-plugins/secret-scrub.js

# Entrypoint: render opencode.json, then run opencode (overrides base's `claude`).
COPY docker/scripts/grok-agent-entrypoint.sh /app/scripts/grok-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/grok-agent-entrypoint.sh

USER agent

LABEL role="grok-runtime"
LABEL description="Grok (xAI) agent runtime — grok-build-0.1 via opencode (OpenAI protocol)"

ENTRYPOINT ["/app/scripts/grok-agent-entrypoint.sh"]
