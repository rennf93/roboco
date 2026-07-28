# Kimi (Moonshot) Agent Image
# =============================================================================
# Runs Kimi K3 through Moonshot's official `kimi` (kimi-code) CLI,
# authenticated by a Kimi subscription via a symlinked-in
# ~/.kimi-code/credentials/kimi-code.json + oauth/ (the shared RW auth mount
# — see roboco.llm.providers.kimi's module docstring) — the parity analogue
# of the Claude Code path's mounted ~/.claude and the codex/gemini paths'
# subscription mounts (no metered API key). Reuses the base image's roboco
# venv + uv + the RoboCo MCP gateway servers. The entrypoint symlinks the
# mounted credential in, renders ~/.kimi-code/config.toml + mcp.json +
# AGENTS.md from the mounted mcp-config.json (see
# roboco.llm.providers.kimi_cli_config), and runs the CLI headless. One
# runtime image serves every one-shot delivery role — role behaviour comes
# from the mounted system prompt / manifest / mcp-config, exactly as on the
# Claude/grok/codex/gemini paths.
#
# V1 scope: no interactive intake/secretary variant of this image exists
# (unlike grok's agent-grok-prompter / agent-grok-secretary) — Kimi is
# one-shot delivery roles only for now.
# =============================================================================

FROM roboco-agent-base

USER root

# Install the official kimi-code CLI. NO version pin (CEO decision,
# 2026-07-28 — latest at build, always adapt: Kimi's CLI is very young and
# has already shipped a breaking rename within a week of release). The
# install script itself SHA-256-verifies the binary it fetches.
# KIMI_INSTALL_DIR splits the binary (/usr/local/bin/kimi — needs root to
# write) from KIMI_CODE_HOME's mutable per-agent state (~/.kimi-code,
# rendered fresh at container start, never baked into the image — see
# roboco.llm.providers.kimi_cli_config): the installer's own default
# co-locates both under ~/.kimi-code, which would mix a writable binary path
# into the exact tree the entrypoint later writes credentials/config into.
# Build-fails-loud verification (a broken install fails the build here, not
# at spawn); the resolved version is captured to both the build log (RUN
# output) and a baked-in file for runtime attribution — Docker has no native
# mechanism to compute a LABEL value from a RUN command's own output, so the
# file is the durable per-image provenance record (a record, not a pin: the
# next build always reinstalls whatever is latest that day).
ENV KIMI_INSTALL_DIR=/usr/local
RUN curl -fsSL https://code.kimi.com/kimi-code/install.sh -o /tmp/kimi-install.sh \
    && bash /tmp/kimi-install.sh \
    && command -v kimi \
    && kimi --version | tee /etc/kimi-cli-version \
    && rm -rf /tmp/*

# Entrypoint: symlink the mounted credential in, render config.toml/mcp.json/
# AGENTS.md, then run kimi headless (overrides the base image's `claude`
# entrypoint). Pre-create + chown ~/.kimi-code (mirrors the gemini image —
# the entrypoint's own symlink/render steps then just write into it).
COPY docker/scripts/kimi-cli-agent-entrypoint.sh /app/scripts/kimi-cli-agent-entrypoint.sh
COPY docker/scripts/kimi-bash-guard-wrapper.sh /app/scripts/kimi-bash-guard-wrapper.sh
RUN chmod 0755 /app/scripts/kimi-cli-agent-entrypoint.sh /app/scripts/kimi-bash-guard-wrapper.sh \
    && mkdir -p /home/agent/.kimi-code \
    && chown -R agent:agent /home/agent/.kimi-code

USER agent

LABEL role="kimi-cli-runtime"
LABEL description="Kimi (Moonshot) agent runtime — Kimi K3 via the official kimi CLI"
LABEL kimi.cli.pinned="false"

# Runtime self-update is pure spawn latency + an unreviewed binary fetch in
# an ephemeral container (an update can't persist anyway) — suppressed at
# the env level; `[upgrade] auto_install=false` in the rendered config.toml
# is the belt-and-suspenders config-level twin (roboco.llm.providers.kimi_cli_config).
ENV KIMI_CODE_NO_AUTO_UPDATE=1

ENTRYPOINT ["/app/scripts/kimi-cli-agent-entrypoint.sh"]
