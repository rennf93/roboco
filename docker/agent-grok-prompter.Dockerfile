# GROK Intake (Prompter) Agent — interactive grok-CLI session on Grok.
# =============================================================================
# The Grok analogue of agent-prompter. Unlike the one-shot Grok runtime (a single
# `grok -p` that exits), this holds a PERSISTENT conversation: it receives the
# human's messages over HTTP (POST /turn on :9000) and, per turn, runs a headless
# `grok -p` that resumes one session id, streaming each reply back to the panel
# via the relay (see roboco.agent_sdk.grok_intake_main + grok_cli_session). The
# intake `propose_draft` tool is wired as the roboco-intake MCP server (rendered
# into ~/.grok/config.toml by the driver). Builds on the Grok runtime image
# (grok CLI + the roboco venv).
# =============================================================================

FROM roboco-agent-grok

LABEL role="grok-prompter"
LABEL description="Intake interviewer on Grok — a panel-driven grok-CLI conversation"

# The in-container receiver the orchestrator delivers the human's turns to.
EXPOSE 9000

# Override the base grok one-shot entrypoint with the interactive intake driver.
ENTRYPOINT ["python", "-m", "roboco.agent_sdk.grok_intake_main"]
