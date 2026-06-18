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

# opencode — the OpenAI-protocol agent runtime. grok-build-0.1 runs on opencode's
# BUILT-IN xai provider (no custom provider npm — that breaks model resolution),
# so only opencode-ai is installed; it resolves the provider SDK at runtime.
RUN npm install -g opencode-ai \
    && npm cache clean --force \
    && rm -rf /root/.npm /tmp/*

# opencode plugins, baked into the AUTO-DISCOVERY dir (~/.config/opencode/plugin/).
# opencode 1.17.8 does NOT register a plugin's hooks/tools from a config
# `plugin:`-array absolute path — only from this directory (verified live). Each
# plugin uses a NAMED export.
#   secret-scrub — bash-guard parity (PAT/credential deny on tool.execute.before)
#   budget-feed  — POSTs budget/loop/terminal counters to the in-container SDK
#                  server (tool.execute.{before,after}); the entrypoint starts
#                  that server (roboco.agent_sdk.server) for Claude-parity.
COPY docker/grok/secret-scrub.js /home/agent/.config/opencode/plugin/secret-scrub.js
COPY docker/grok/budget-feed.js /home/agent/.config/opencode/plugin/budget-feed.js

# Entrypoint: render opencode.json, then run opencode (overrides base's `claude`).
COPY docker/scripts/grok-agent-entrypoint.sh /app/scripts/grok-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/grok-agent-entrypoint.sh

# opencode persists data under ~/.local/share and state under ~/.local/state, and
# reads config + plugins from ~/.config/opencode. When the orchestrator
# bind-mounts the opencode store at ~/.local/share/opencode, docker creates the
# intermediate ~/.local AS ROOT, so the non-root agent can no longer create its
# siblings and opencode EACCESes at boot. Pre-create the trees agent-owned so the
# mount leaves the parents writable (complements the orchestrator's 0777
# host-source pre-create), and so the baked plugin dir is agent-owned.
RUN mkdir -p /home/agent/.local/share/opencode /home/agent/.local/state \
        /home/agent/.config/opencode/plugin \
    && chown -R agent:agent /home/agent/.local /home/agent/.config

USER agent

LABEL role="grok-runtime"
LABEL description="Grok (xAI) agent runtime — grok-build-0.1 via opencode (OpenAI protocol)"

ENTRYPOINT ["/app/scripts/grok-agent-entrypoint.sh"]
