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

# Install the official grok CLI (Grok Build) for the agent user. The installer's
# default is $HOME/.grok/bin, so the binary lands at ~/.grok/bin/grok alongside
# its runtime (downloads / bundled / skills) under ~/.grok, all agent-owned.
# Pinned — untrusted model output runs under it, so bump the version deliberately,
# never float. Download the installer to a file first (a `curl | bash` pipe hides
# a curl failure as a silent no-op) and verify the binary installed AND runs, so
# a broken install fails the build here, not at spawn. (curl/bash from the base.)
ARG GROK_CLI_VERSION=0.2.56
RUN su agent -s /bin/bash -c "set -euo pipefail; export HOME=/home/agent; \
      curl -fsSL https://x.ai/cli/install.sh -o /tmp/grok-install.sh; \
      bash /tmp/grok-install.sh ${GROK_CLI_VERSION}; \
      test -x /home/agent/.grok/bin/grok; \
      /home/agent/.grok/bin/grok --version" \
    && rm -rf /tmp/*

# Entrypoint: render ~/.grok/config.toml + the per-role flags, then run grok
# headless (overrides the base image's `claude` entrypoint). ~/.grok is already
# agent:agent-owned (installed above via `su agent`), so no chown needed here.
COPY docker/scripts/grok-cli-agent-entrypoint.sh /app/scripts/grok-cli-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/grok-cli-agent-entrypoint.sh

USER agent

# grok installs to ~/.grok/bin; put it ahead of the venv on PATH so the
# entrypoint finds `grok` (and still resolves `python` to /app/.venv/bin).
ENV PATH="/home/agent/.grok/bin:/app/.venv/bin:$PATH"

LABEL role="grok-cli-runtime"
LABEL description="Grok (xAI) agent runtime — Grok Build via the official grok CLI"

ENTRYPOINT ["/app/scripts/grok-cli-agent-entrypoint.sh"]
