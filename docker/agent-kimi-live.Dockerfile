# kimi LIVE-CHAT agent - interactive intake/secretary session on kimi.
# =============================================================================
# The generic analogue of agent-grok-secretary: holds a PERSISTENT
# conversation (POST /turn on :9000), one headless CLI invocation per turn,
# streaming each reply to the panel via the relay. Tools reach the CLI the
# same way the one-shot path wires them (rendered mcpServers; the rendered pi
# extension for hummin). Builds on the kimi runtime image (CLI + roboco venv).
# Both roles ride this ONE image - ROBOCO_AGENT_ROLE selects the behavior.
# =============================================================================

FROM roboco-agent-kimi

LABEL role="kimi-live"
LABEL description="kimi interactive live chat (intake + secretary, generic driver)"

# The in-container receiver the orchestrator delivers turns to.
EXPOSE 9000

# Override the one-shot entrypoint: render config, then the generic driver.
ENTRYPOINT ["/app/scripts/entrypoints/kimi-live-entrypoint.sh"]
