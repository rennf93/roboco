# GROK Secretary Agent — interactive opencode-serve session on Grok.
# =============================================================================
# The Grok analogue of agent-secretary. Holds a PERSISTENT `opencode serve`
# session open, receives the CEO's messages over HTTP (POST /turn on :9000), and
# streams each reply back to the panel via the relay. The Secretary's CEO-
# authority tools (read_company_state / read_task / submit_directive) are
# registered as opencode tools by the secretary-tools.js plugin, which calls
# /api/secretary/* with the container's HMAC agent token. Builds on the Grok
# runtime image; the driver renders opencode.json from the spawn env first.
# =============================================================================

FROM roboco-agent-grok

USER root

# The CEO-authority tool plugin (read_company_state / read_task / submit_directive),
# baked into the auto-discovery dir so ONLY the Secretary image carries it (no
# other role gets CEO authority). opencode registers it from this directory; a
# config `plugin:`-array path would not register its tools (verified live).
COPY docker/grok/secretary-tools.js /home/agent/.config/opencode/plugin/secretary-tools.js
RUN chown agent:agent /home/agent/.config/opencode/plugin/secretary-tools.js

USER agent

LABEL role="grok-secretary"
LABEL description="Secretary on Grok — a long-lived opencode serve session driven by the panel"

# The in-container receiver the orchestrator delivers the CEO's turns to.
EXPOSE 9000

# Override the base grok one-shot entrypoint with the interactive secretary driver.
ENTRYPOINT ["python", "-m", "roboco.agent_sdk.grok_secretary_main"]
