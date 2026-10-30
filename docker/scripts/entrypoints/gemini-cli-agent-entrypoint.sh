#!/usr/bin/env bash
# Entrypoint for the roboco-agent-gemini image (one-shot delivery roles).
#
# Runs an agent on Google's official `gemini` CLI, authenticated by an OAuth
# login via a mounted ~/.gemini/oauth_creds.json — the parity analogue of the
# Claude Code path's mounted ~/.claude and the grok path's mounted ~/.grok. The
# gateway, identity, and workspace are mounted by the orchestrator's shared
# container assembly (the same that wires Claude/grok); this entrypoint copies
# the staged OAuth credential into a writable ~/.gemini, renders the gemini
# runtime config from that mount, and runs the CLI headless.
set -euo pipefail

# The orchestrator mounts the host ~/.gemini DIRECTORY read-only at this
# staging path (roboco.llm.providers.gemini._append_gemini_auth_mount). Copy it
# into the image's own ~/.gemini (agent-owned, writable — see the Dockerfile)
# so the CLI's in-process OAuth refresh (google-auth-library) can write the
# refreshed token back locally: Google's refresh token is REUSABLE, so each
# container refreshing its OWN copy independently is safe (contrast grok's
# live-symlinked RO mount, which needs single-writer orchestrator-side
# serialization because xAI's refresh token is single-use — see
# roboco.llm.providers.gemini's module docstring). The host's copy is never
# touched.
AUTH_STAGING_DIR="/home/agent/.gemini-auth-ro"
if [ -d "$AUTH_STAGING_DIR" ]; then
  cp -a "$AUTH_STAGING_DIR"/. /home/agent/.gemini/ 2>/dev/null || true
fi

# Auth preflight. Without a real OAuth credential the CLI would hang at an
# interactive consent prompt in a headless container (or refuse outright,
# depending on the auth-check path) — refuse fast instead: exit 41 (the CLI's
# own dedicated auth-failure code, so the orchestrator's exit classifier
# treats a missing credential identically to a real CLI auth rejection).
if [ ! -s /home/agent/.gemini/oauth_creds.json ]; then
  echo "[gemini] OAuth credential missing at ~/.gemini/oauth_creds.json — refusing" \
    "to run. Run 'gemini' interactively once on the host (or set" \
    "ROBOCO_HOST_GEMINI_DIR at the directory holding oauth_creds.json) before" \
    "spawning Gemini agents." >&2
  exit 41
fi

# Render ~/.gemini/settings.json (mcpServers + selectedType/enableAgents/
# autoConfigureMemory) + GEMINI.md + the Policy Engine TOML. Run from /app so
# `python -m` resolves the INSTALLED roboco package: dev/doc/qa agents run at
# their workspace-clone cwd, whose own roboco/ dir would shadow it on the
# sys.path front (the ModuleNotFound lesson). The render reads
# ROBOCO_MCP_CONFIG + ROBOCO_AGENT_ID and writes the config files.
( cd /app && python -m roboco.llm.providers.gemini_cli_config )

# Prompt-injection guard (parity with the Claude UserPromptSubmit hook / the
# grok path): the task prompt is DATA, not instructions — refuse a poisoned
# one before the model sees it. Same patterns as
# docker/scripts/user-prompt-hook.sh; run from /app too.
if ! ( cd /app && python -m roboco.agent_sdk.prompt_guard "${ROBOCO_INITIAL_PROMPT:-}" ); then
  echo "Refusing to run: task prompt matched a prompt-injection pattern." >&2
  exit 1
fi

# MCP-dead-server mitigation. The Gemini CLI has no native fail-fast for a
# dead MCP server (a disconnected server just logs DISCONNECTED and the run
# continues, tool-less) — so this cheap out-of-band check (the same
# gateway-venv import probe the orchestrator's reaper uses,
# `_probe_gateway_health`) catches a corrupted /app/.venv BEFORE the run
# starts, exiting 52 (EX_CONFIG-style: config/environment broken, not a task
# failure) instead of burning a whole run against a tool-less gateway.
if ! /app/.venv/bin/python -c "import httpx, mcp" 2>/dev/null; then
  echo "[gemini] MCP gateway venv is broken (httpx/mcp import failed) — refusing" \
    "to run against a dead gateway." >&2
  exit 52
fi

# Run the agent. The prompt comes from an env var (never an untrusted argv
# positional). `< /dev/null` keeps the headless run from blocking on stdin. We
# do NOT `exec`: the script regains control to classify the exit code +
# capture usage. `--output-format stream-json` + `tee` streams the run to the
# container's stdout LIVE (so `docker logs` shows the agent reasoning /
# answering in real time, parity with the Claude/grok paths' stream-json)
# while ALSO capturing it to RUN_LOG for the usage-stats / exit-classification
# reads below. stderr goes to ERR_LOG and is surfaced after the run.
RUN_LOG="/tmp/gemini-run.json"
ERR_LOG="/tmp/gemini-run.err"
# gemini_cli_config (rendered above) already wrote the per-role CLI flag
# tokens (today: just --approval-mode yolo — tool scoping lives in
# settings.json/policy TOML, not CLI flags) one per line to this file.
GEMINI_ARGS_FILE="${ROBOCO_GEMINI_ARGS_FILE:-/tmp/roboco-gemini-args}"
mapfile -t GEMINI_ARGS < "$GEMINI_ARGS_FILE"

set +e
gemini -p "${ROBOCO_INITIAL_PROMPT:-}" \
  -m "${ROBOCO_AGENT_MODEL:-gemini-2.5-pro}" \
  --output-format stream-json \
  "${GEMINI_ARGS[@]}" \
  < /dev/null 2> "$ERR_LOG" | tee "$RUN_LOG"
run_rc=${PIPESTATUS[0]}
set -e
# stdout already streamed live via tee; surface stderr (tool calls / errors) too.
[ -s "$ERR_LOG" ] && cat "$ERR_LOG" >&2

# Capture token usage from the run's own captured stdout (no session-file
# scraping — Gemini reports per-model stats directly, unlike grok). Writes a
# usage.json the orchestrator reads back at finalize. Best-effort; never fails
# the run. Run from /app for the same module-resolution reason as the render
# above.
( cd /app && ROBOCO_GEMINI_RUN_LOG="$RUN_LOG" \
    python -m roboco.llm.providers.gemini_cli_usage ) || true

# Exit-code classification. 41 (auth) is the CLI's own dedicated exit code and
# passes through unchanged. A quota/rate-limit error has NO dedicated CLI exit
# code — it falls to the CLI's generic 1 — so this remaps it to 75 (EX_TEMPFAIL)
# by parsing the run's captured JSON for a quota-error `error.type`
# (TerminalQuotaError / RetryableQuotaError), the parity analogue of grok's
# text-grep exit-75 detector. The orchestrator parks the GEMINI provider on 75
# instead of the dispatcher respawning the same task every tick.
classified_rc=$(cd /app && ROBOCO_GEMINI_RUN_LOG="$RUN_LOG" \
    ROBOCO_GEMINI_CLI_EXIT_CODE="$run_rc" \
    python -m roboco.llm.providers.gemini_cli_usage --classify-exit)
if [ "$classified_rc" != "$run_rc" ]; then
  echo "[gemini] exit $run_rc reclassified to $classified_rc (quota/rate-limit" \
    "detected in the run output) — the orchestrator parks the provider; the" \
    "task is retried when the limit lifts." >&2
fi

# A graceful exit without a terminal verb is handled server-side by the
# orchestrator (_handle_stopped_container substitutes the still-owned task) —
# the gemini-cli runtime needs no in-container SDK server for that.
exit "$classified_rc"
