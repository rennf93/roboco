#!/bin/bash
# A2A Check Hook (retired)
#
# This hook once polled the agent SDK's /inbox/count endpoint to hint at
# pending A2A messages. That endpoint (and the whole in-container priority
# inbox scaffold) was deleted: agent-to-agent DMs are pull-only by design,
# read through the read_a2a verb, and steering-marked messages are rendered
# by the orchestrator into the recipient's next context boundary (spawn
# briefing / live turn queue), never pushed into the container.
#
# The hook stays registered in spawn_config and the agent image, so it
# remains a non-blocking no-op instead of breaking the hook wiring.

# Always exit 0 - don't block Claude
exit 0
