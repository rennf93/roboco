# Codex (OpenAI) Agent Image
# =============================================================================
# Runs OpenAI's Codex agent through the official `codex` CLI, authenticated by a
# ChatGPT subscription via a mounted ~/.codex/auth.json — the parity analogue of
# the Grok path's mounted ~/.grok (no metered API key). Reuses the base image's
# roboco venv + uv + the RoboCo MCP gateway servers. The entrypoint renders
# ~/.codex/config.toml (the gateway) + the execpolicy deny rules + the per-role
# sandbox flag from the mounted mcp-config.json (see
# roboco.llm.providers.codex_cli_config) and runs the CLI headless. One runtime
# image serves every one-shot delivery role — role behaviour comes from the
# mounted system prompt / manifest / mcp-config, exactly as on the grok path.
#
# V1 scope: no interactive intake/secretary variant of this image exists (unlike
# grok's agent-grok-prompter / agent-grok-secretary) — Codex is one-shot delivery
# roles only for now.
# =============================================================================

FROM roboco-agent-base

USER root

# Install the official codex CLI for the agent user. Pinned — untrusted model
# output runs under it, so bump the version deliberately, never float. Download
# the installer to a file first (a `curl | bash` pipe hides a curl failure as a
# silent no-op) and verify the binary installed AND runs, so a broken install
# fails the build here, not at spawn. (curl/bash from the base.)
ARG CODEX_CLI_VERSION=0.145.0
RUN su agent -s /bin/bash -c "set -euo pipefail; export HOME=/home/agent; \
      curl -fsSL https://chatgpt.com/codex/install.sh -o /tmp/codex-install.sh; \
      bash /tmp/codex-install.sh ${CODEX_CLI_VERSION}; \
      test -x /home/agent/.codex/bin/codex || command -v codex; \
      codex --version" \
    && rm -rf /tmp/*

# Entrypoint: render ~/.codex/config.toml + execpolicy rules + the per-role
# sandbox flag, then run codex headless (overrides the base image's `claude`
# entrypoint). ~/.codex is already agent:agent-owned (installed above via
# `su agent`), so no chown needed here.
COPY docker/scripts/codex-cli-agent-entrypoint.sh /app/scripts/codex-cli-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/codex-cli-agent-entrypoint.sh

USER agent

# codex installs to ~/.codex/bin (or ~/.local/bin, depending on the installer);
# put both ahead of the venv on PATH so the entrypoint finds `codex` (and still
# resolves `python` to /app/.venv/bin).
ENV PATH="/home/agent/.codex/bin:/home/agent/.local/bin:/app/.venv/bin:$PATH"

LABEL role="codex-cli-runtime"
LABEL description="Codex (OpenAI) agent runtime — Codex Build via the official codex CLI"

ENTRYPOINT ["/app/scripts/codex-cli-agent-entrypoint.sh"]
