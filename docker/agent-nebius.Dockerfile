# Nebius Token Factory Agent Image
# =============================================================================
# Runs Token Factory's open models (NVIDIA Nemotron, DeepSeek, Qwen, ...)
# through the opencode CLI (sst/opencode), authenticated by a static metered
# API key injected via env (NEBIUS_API_KEY + NEBIUS_BASE_URL) - the Ollama
# shape, NOT the subscription-mount shape of kimi/codex/gemini/grok (no ~/
# auth mount, no refresh loop). Reuses the base image's roboco venv + uv +
# the RoboCo MCP gateway servers. The entrypoint renders opencode's global
# config (~/.config/opencode/) from the mounted
# mcp-config.json + system-prompt.md (see
# roboco.llm.providers.nebius_cli_config) and runs `opencode run` headless.
# One runtime image serves every one-shot delivery role - role behaviour comes
# from the mounted system prompt / manifest / mcp-config, exactly as on the
# Claude/kimi/codex/gemini/openrouter paths.
#
# V1 scope: no interactive intake/secretary variant of this image exists -
# Nebius is one-shot delivery roles only for now.
# =============================================================================

FROM roboco-agent-base

USER root

# Install the official opencode CLI via npm. NO version pin (CEO decision,
# 2026-07-28 - latest at build, always adapt: the opencode CLI is young and
# its JSON event schema may ship breaking changes within a release cycle).
# Build-fails-loud verification (a broken install fails the build here, not
# at spawn); the resolved version is captured to both the build log (RUN
# output) and a baked-in file for runtime attribution - Docker has no native
# mechanism to compute a LABEL value from a RUN command's own output, so the
# file is the durable per-image provenance record (a record, not a pin: the
# next build always reinstalls whatever is latest that day).
RUN npm install -g opencode-ai \
    && command -v opencode \
    && opencode --version | tee /etc/opencode-cli-version \
    && rm -rf /tmp/*

# Entrypoint: render opencode's global config (opencode.json + the
# bash-guard plugin under ~/.config/opencode/plugins/, wiring the
# tool.execute.before hook - a genuine PreToolUse-equivalent - to
# /app/scripts/hooks/bash-guard-hook.sh from the base image), then run opencode
# headless (overrides the base image's `claude` entrypoint). The
# permission.bash deny-rules in the rendered opencode.json are the primary
# gate; the plugin is defense-in-depth (see
# roboco.llm.providers.nebius_cli_config).
COPY docker/scripts/entrypoints/nebius-agent-entrypoint.sh /app/scripts/entrypoints/nebius-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/entrypoints/nebius-agent-entrypoint.sh

USER agent

LABEL role="nebius-cli-runtime"
LABEL description="Nebius Token Factory agent runtime - Nemotron + 60 open models via the opencode CLI"
LABEL opencode.cli.pinned="false"

ENTRYPOINT ["/app/scripts/entrypoints/nebius-agent-entrypoint.sh"]
