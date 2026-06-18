# GROK Intake (Prompter) Agent — interactive opencode-serve session on Grok.
# =============================================================================
# The Grok analogue of agent-prompter. Unlike the one-shot Grok runtime (a
# single `opencode run` that exits), this holds a PERSISTENT `opencode serve`
# session open, receives the human's messages over HTTP (POST /turn on :9000),
# and streams each reply back to the panel via the relay. Builds on the Grok
# runtime image (opencode + @ai-sdk/openai + the secret-scrub plugin); the
# driver renders opencode.json from the spawn env, then drives the session.
# =============================================================================

FROM roboco-agent-grok

USER root

# The intake propose_draft tool plugin (the model calls it; the driver turns the
# call into the panel's draft card), baked into the auto-discovery dir so only
# the intake image carries it. opencode registers tools from this directory, not
# from a config `plugin:`-array path (verified live).
COPY docker/grok/intake-tools.js /home/agent/.config/opencode/plugin/intake-tools.js
RUN chown agent:agent /home/agent/.config/opencode/plugin/intake-tools.js

USER agent

LABEL role="grok-prompter"
LABEL description="Intake interviewer on Grok — a long-lived opencode serve session driven by the panel"

# The in-container receiver the orchestrator delivers the human's turns to.
EXPOSE 9000

# Override the base grok one-shot entrypoint with the interactive intake driver.
ENTRYPOINT ["python", "-m", "roboco.agent_sdk.grok_intake_main"]
