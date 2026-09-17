# Hummin (GLM-native CLI) Agent Image
# =============================================================================
# Runs the GLM family through the GLM-native `hummin` CLI (a pi-harness
# fork), authenticated by the operator's Z.ai key for the GLM Coding Plan —
# injected as ZAI_API_KEY at spawn (the OpenRouter shape: a stored key, NO
# ~/.hummin credential mount, no refresh loop). Reuses the base image's
# roboco venv + uv + Node.js. The entrypoint renders ~/.hummin/agent/
# settings.json + the per-role tools env file (see
# roboco.llm.providers.hummin_cli_config), runs hummin's own auth preflight,
# and runs `hummin --mode json` headless. One runtime image serves every
# one-shot delivery role — role behaviour comes from the mounted system
# prompt / manifest / mcp-config, exactly as on the kimi/grok/codex paths.
#
# V1 scope: no interactive intake/secretary variant of this image exists —
# hummin is one-shot delivery roles only for now. hummin has NO MCP client
# (see roboco.llm.providers.hummin's module docstring): the mounted
# mcp-config.json rides along inert and the RoboCo gateway is unreachable
# in V1.
# =============================================================================

FROM roboco-agent-base

USER root

# Install the GLM-native hummin CLI. NO version pin (fleet policy,
# 2026-07-28 — latest at build, always adapt; the resolved version is
# stamped below for provenance). The base image ships Node.js 22.x from
# NodeSource; hummin requires >= 22.19, so the build FAILS LOUD here if a
# future base rebuild ever ships an older minor (a broken install must fail
# the build, not the first spawn).
RUN NODE_MIN="22.19" \
    && NODE_CUR="$(node -p 'process.versions.node.split(".").slice(0, 2).join(".")')" \
    && [ "$(printf '%s\n%s\n' "$NODE_MIN" "$NODE_CUR" | sort -V | head -n1)" = "$NODE_MIN" ] \
    || { echo "hummin needs node >= ${NODE_MIN}; base image has ${NODE_CUR}" >&2; exit 1; }
RUN npm install -g hummin-cli \
    && command -v hummin \
    && hummin --version | tee /etc/hummin-cli-version \
    && npm cache clean --force \
    && rm -rf /tmp/*

# Entrypoint: render the hummin runtime config, run the auth preflight, then
# run the CLI headless (overrides the base image's `claude` entrypoint).
# Pre-create + chown ~/.hummin/agent (mirrors the kimi image — the
# entrypoint's own render steps then just write into it).
COPY docker/scripts/entrypoints/hummin-cli-agent-entrypoint.sh /app/scripts/entrypoints/hummin-cli-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/entrypoints/hummin-cli-agent-entrypoint.sh \
    && mkdir -p /home/agent/.hummin/agent \
    && chown -R agent:agent /home/agent/.hummin

USER agent

LABEL role="hummin-cli-runtime"
LABEL description="GLM agent runtime — GLM 5.3 family via the GLM-native hummin CLI"
LABEL hummin.cli.pinned="false"

ENTRYPOINT ["/app/scripts/entrypoints/hummin-cli-agent-entrypoint.sh"]
