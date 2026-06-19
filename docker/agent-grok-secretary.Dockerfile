# GROK Secretary Agent — interactive grok-CLI session on Grok.
# =============================================================================
# The Grok analogue of agent-secretary. Holds a PERSISTENT conversation: receives
# the CEO's messages over HTTP (POST /turn on :9000) and, per turn, runs a
# headless `grok -p` that resumes one session id, streaming each reply back to the
# panel via the relay. The Secretary's CEO-authority tools (read_company_state /
# read_task / submit_directive) are wired as the roboco-secretary MCP server
# (rendered into ~/.grok/config.toml by the driver), which calls /api/secretary/*
# with the container's HMAC agent token. Builds on the Grok runtime image
# (grok CLI + the roboco venv).
# =============================================================================

FROM roboco-agent-grok

LABEL role="grok-secretary"
LABEL description="Secretary on Grok — a panel-driven grok-CLI conversation"

# The in-container receiver the orchestrator delivers the CEO's turns to.
EXPOSE 9000

# Override the base grok one-shot entrypoint with the interactive secretary driver.
ENTRYPOINT ["python", "-m", "roboco.agent_sdk.grok_secretary_main"]
