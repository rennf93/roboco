# Grok (xAI) Agent Image
# =============================================================================
# Runs Grok Build through xAI's official `grok` CLI, authenticated by the
# SuperGrok subscription via a mounted ~/.grok/auth.json — the parity analogue of
# the Claude Code path's mounted ~/.claude (no metered API key). Reuses the base
# image's roboco venv + uv + the RoboCo MCP gateway servers. The entrypoint
# renders ~/.grok/config.toml (the gateway) + the per-role grok flags from the
# mounted mcp-config.json (see roboco.llm.providers.grok_cli_config) and runs the
# CLI headless. One runtime image serves every role — role behaviour comes from
# the mounted system prompt / manifest / mcp-config, exactly as on the Claude path.
# =============================================================================

FROM roboco-agent-base

USER root

# Install the official grok CLI (Grok Build) for the agent user: the binary lands
# at ~/.local/bin/grok and its runtime (bin / bundled / skills) at ~/.grok, both
# agent-owned. Pinned — untrusted model output runs under it, so bump the version
# deliberately, never float. (curl + bash are provided by roboco-agent-base.)
ARG GROK_CLI_VERSION=0.2.56
RUN su agent -s /bin/bash -c "export HOME=/home/agent; \
      curl -fsSL https://x.ai/cli/install.sh | bash -s ${GROK_CLI_VERSION}" \
    && rm -rf /tmp/*

# Entrypoint: render ~/.grok/config.toml + the per-role flags, then run grok
# headless (overrides the base image's `claude` entrypoint).
COPY docker/scripts/grok-cli-agent-entrypoint.sh /app/scripts/grok-cli-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/grok-cli-agent-entrypoint.sh \
    && chown -R agent:agent /home/agent/.grok /home/agent/.local

USER agent

# grok installs to ~/.local/bin; put it ahead of the venv on PATH so the
# entrypoint finds `grok` (and still resolves `python` to /app/.venv/bin).
ENV PATH="/home/agent/.local/bin:/app/.venv/bin:$PATH"

LABEL role="grok-cli-runtime"
LABEL description="Grok (xAI) agent runtime — Grok Build via the official grok CLI"

ENTRYPOINT ["/app/scripts/grok-cli-agent-entrypoint.sh"]
