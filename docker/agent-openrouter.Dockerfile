# OpenRouter Agent Image
# =============================================================================
# Runs any of OpenRouter's models through the opencode CLI (sst/opencode),
# authenticated by a static metered API key injected via env
# (OPENROUTER_API_KEY + OPENROUTER_BASE_URL) — the Ollama shape, NOT the
# subscription-mount shape of kimi/codex/gemini/grok (no ~/. auth mount, no
# refresh loop). Reuses the base image's roboco venv + uv + the RoboCo MCP
# gateway servers. The entrypoint renders opencode.json from the mounted
# mcp-config.json + system-prompt.md (see
# roboco.llm.providers.openrouter_cli_config) and runs `opencode run` headless.
# One runtime image serves every one-shot delivery role — role behaviour comes
# from the mounted system prompt / manifest / mcp-config, exactly as on the
# Claude/kimi/codex/gemini paths.
#
# V1 scope: no interactive intake/secretary variant of this image exists —
# OpenRouter is one-shot delivery roles only for now.
# =============================================================================

FROM roboco-agent-base

USER root

# Install the official opencode CLI via npm. NO version pin (CEO decision,
# 2026-07-28 — latest at build, always adapt: the opencode CLI is young and
# its JSON event schema may ship breaking changes within a release cycle).
# Build-fails-loud verification (a broken install fails the build here, not
# at spawn); the resolved version is captured to both the build log (RUN
# output) and a baked-in file for runtime attribution — Docker has no native
# mechanism to compute a LABEL value from a RUN command's own output, so the
# file is the durable per-image provenance record (a record, not a pin: the
# next build always reinstalls whatever is latest that day).
RUN npm install -g opencode-ai \
    && command -v opencode \
    && opencode --version | tee /etc/opencode-cli-version \
    && rm -rf /tmp/*

# Entrypoint: render opencode.json (agent prompt + permission deny-rules + mcp
# + OpenRouter provider), then run opencode headless (overrides the base
# image's `claude` entrypoint). No bash-guard wrapper — opencode has no
# PreToolUse hook; the permission.bash deny-rules in the rendered opencode.json
# ARE the bash-guard (see roboco.llm.providers.openrouter_cli_config).
COPY docker/scripts/openrouter-agent-entrypoint.sh /app/scripts/openrouter-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/openrouter-agent-entrypoint.sh

USER agent

LABEL role="openrouter-cli-runtime"
LABEL description="OpenRouter agent runtime — any OpenRouter model via the opencode CLI"
LABEL opencode.cli.pinned="false"

ENTRYPOINT ["/app/scripts/openrouter-agent-entrypoint.sh"]