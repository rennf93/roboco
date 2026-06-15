# Secretary Agent — the persistent Claude Code session the CEO chats with as
# their chief-of-staff.
#
# Like the intake (prompter) agent it runs a long-lived driver holding one
# `claude-agent-sdk` `ClaudeSDKClient` open, receiving the CEO's messages over
# HTTP (POST /turn) and streaming each reply to the panel. Unlike intake, its
# tools call the backend `/api/secretary/*` routes to read company state and
# submit directives (the gate-list bounces high-impact ones back to the CEO).
# `claude-agent-sdk` and `roboco` are already in the base image; the SDK drives
# the same `claude` binary using the same mounted ~/.claude auth — no API key.
FROM roboco-agent-base

LABEL role="secretary"
LABEL description="CEO's chief-of-staff — a long-lived Claude Agent SDK session with gated CEO authority"

# Override the base `["claude"]` entrypoint with the secretary driver. WORKDIR
# /app and the venv on PATH are inherited from the base; roboco lives at /app/roboco.
ENTRYPOINT ["python", "-m", "roboco.agent_sdk.secretary_main"]
