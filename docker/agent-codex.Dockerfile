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

# Install the official codex CLI globally via npm. Pinned — untrusted model
# output runs under it, so bump the version deliberately, never float. The
# npm route (not chatgpt.com/codex/install.sh, which the CDN denies to
# non-browser clients) has no postinstall network fetch: the native binary
# rides an optionalDependency (@openai/codex-linux-x64) served from the
# public npm registry. Global install symlinks `codex` onto PATH for the
# agent user; verify it runs so a broken install fails the build, not spawn.
ARG CODEX_CLI_VERSION=0.145.0
RUN npm install -g @openai/codex@${CODEX_CLI_VERSION} \
    && command -v codex \
    && codex --version

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
