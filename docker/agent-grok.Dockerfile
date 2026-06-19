# =============================================================================
# Agent Grok Image (xAI / Grok Build CLI)
# =============================================================================
# Extends the agent-base (which has Python, uv, all hook scripts, SDK).
# Installs the official Grok CLI (@xai-official/grok or install script) and
# overrides entrypoint with a wrapper that ensures the full set of runtime
# hooks + SDK server are active for Grok sessions (sdk-startup first, then
# grok -p using the mounted ~/.grok/user-settings.json that carries the hooks).
# =============================================================================

FROM roboco-agent-base

# Install Grok CLI. Prefer npm for reliability in build envs; fallback to
# official curl installer. The CLI binary is "grok".
RUN npm install -g @xai-official/grok 2>/dev/null || \
    (curl -fsSL https://x.ai/cli/install.sh -o /tmp/grok-install.sh && \
     bash /tmp/grok-install.sh) || \
    echo "Grok CLI install attempted; verify grok binary in PATH at runtime"

# Grok entrypoint wrapper (provides SDK startup + briefing + full hooks activation via settings)
COPY docker/scripts/grok-cli-agent-entrypoint.sh /app/scripts/grok-cli-agent-entrypoint.sh
RUN chmod 0755 /app/scripts/grok-cli-agent-entrypoint.sh

# Prepare ~/.grok so that the mounted user-settings.json is discoverable
# even before the first grok run (grok inspect / hooks loader expects it).
RUN mkdir -p /home/agent/.grok && chown -R agent:agent /home/agent || true

USER agent

# Entrypoint runs sdk hooks then exec grok with orchestrator-supplied flags.
ENTRYPOINT ["/app/scripts/grok-cli-agent-entrypoint.sh"]
