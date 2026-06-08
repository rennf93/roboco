# Intake (Prompter) Agent — the interactive Claude Code session the CEO chats with.
#
# Unlike every other agent (a one-shot `claude -p` that does a task and exits),
# the intake agent runs a PERSISTENT driver: it holds one `claude-agent-sdk`
# `ClaudeSDKClient` open, receives the human's messages over HTTP (POST /turn),
# and streams each reply back to the panel. `claude-agent-sdk` is already in the
# base image (it's a main dependency); the SDK drives the same `claude` binary
# the base ships, using the same mounted ~/.claude auth — no API key.
FROM roboco-agent-base

LABEL role="prompter"
LABEL description="Intake interviewer — a long-lived Claude Agent SDK session driven by the panel"

# Override the base `["claude"]` entrypoint with the intake driver. WORKDIR /app
# and the venv on PATH are inherited from the base; roboco lives at /app/roboco.
ENTRYPOINT ["python", "-m", "roboco.agent_sdk.intake_main"]
