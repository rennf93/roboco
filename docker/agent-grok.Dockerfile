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

# opencode — the OpenAI-protocol agent runtime. The @ai-sdk/openai-compatible
# package backs the custom xAI provider declared in the generated opencode.json;
# opencode also resolves it at runtime, but pre-installing keeps first spawn off
# the network.
RUN npm install -g opencode-ai @ai-sdk/openai-compatible \
    && npm cache clean --force \
    && rm -rf /root/.npm /tmp/*

# Entrypoint: render opencode.json, then run opencode (overrides base's `claude`).
COPY docker/scripts/grok-agent-entrypoint.sh /app/scripts/grok-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/grok-agent-entrypoint.sh

USER agent

LABEL role="grok-runtime"
LABEL description="Grok (xAI) agent runtime — grok-build-0.1 via opencode (OpenAI protocol)"

ENTRYPOINT ["/app/scripts/grok-agent-entrypoint.sh"]
