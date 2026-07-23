# Gemini (Google) Agent Image
# =============================================================================
# Runs Gemini through Google's official `gemini` CLI, authenticated by an OAuth
# login via a mounted ~/.gemini/oauth_creds.json — the parity analogue of the
# Claude Code path's mounted ~/.claude and the grok path's mounted ~/.grok (no
# metered API key). Reuses the base image's roboco venv + uv + the RoboCo MCP
# gateway servers, and the base image's Node.js 22 (the CLI needs node >= 20).
# The entrypoint copies the staged read-only OAuth credential into a writable
# ~/.gemini, renders ~/.gemini/settings.json + a Policy Engine TOML from the
# mounted mcp-config.json (see roboco.llm.providers.gemini_cli_config), and
# runs the CLI headless. One runtime image serves every role — role behaviour
# comes from the mounted system prompt / manifest / mcp-config, exactly as on
# the Claude/grok paths.
# =============================================================================

FROM roboco-agent-base

USER root

# Install the official Gemini CLI. Pinned — untrusted model output runs under
# it, so bump the version deliberately, never float (spike verified 0.52.0 at
# github.com/google-gemini/gemini-cli @ 9681621c). npm installs to the global
# node_modules the base image's Node 22 already resolves onto PATH.
ARG GEMINI_CLI_VERSION=0.52.0
RUN npm install -g "@google/gemini-cli@${GEMINI_CLI_VERSION}" \
    && npm cache clean --force \
    && rm -rf /root/.npm /tmp/* \
    && gemini --version

# Entrypoint: copy the staged OAuth credential into a writable ~/.gemini,
# render settings.json + policy TOML, then run gemini headless (overrides the
# base image's `claude` entrypoint). Owned by agent (mirrors the grok image).
COPY docker/scripts/gemini-cli-agent-entrypoint.sh /app/scripts/gemini-cli-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/gemini-cli-agent-entrypoint.sh \
    && mkdir -p /home/agent/.gemini \
    && chown -R agent:agent /home/agent/.gemini

USER agent

LABEL role="gemini-cli-runtime"
LABEL description="Gemini (Google) agent runtime — Gemini Build via the official gemini CLI"

# advanced.autoConfigureMemory=false (rendered into settings.json) pins Node's
# heap sizing away from auto-detection against a shared host; this bounds it
# explicitly instead. Tunable per-deploy without a rebuild.
ENV NODE_OPTIONS="--max-old-space-size=2048"

ENTRYPOINT ["/app/scripts/gemini-cli-agent-entrypoint.sh"]
