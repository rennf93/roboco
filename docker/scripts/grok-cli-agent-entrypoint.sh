#!/usr/bin/env bash
# Entrypoint for the roboco-agent-grok-cli image (one-shot delivery roles).
#
# Runs an agent on xAI's official `grok` CLI (Grok Build), authenticated by the
# SuperGrok subscription via a mounted ~/.grok/auth.json — the parity analogue of
# the Claude Code path's mounted ~/.claude. The gateway, identity, and workspace
# are mounted by the orchestrator's shared container assembly (the same that
# wires Claude); this entrypoint renders the grok runtime config from that mount
# and runs the CLI headless.
#
# SDK server is started here (before grok -p) so that flow/do MCP servers,
# budget counters, circuit breakers and journal post-mortems are available for
# Grok one-shot containers (which bypass Claude's SessionStart hook).
set -euo pipefail

SDK_PORT="${ROBOCO_SDK_PORT:-9000}"
SDK_URL="http://localhost:${SDK_PORT}"

# --- SDK server bring-up (before grok -p; Claude parity) -------------------
# Mirrors sdk-startup-hook.sh and the proven opencode-era Grok SDK start.
# Use bare `python` (from the image venv on PATH) — never `uv run` here:
# the entrypoint cwd may be the workspace clone; uv run would discover the
# project and re-sync the drifted lock (stall + 350MB download). The venv
# python is the baked one with all deps.
if ! curl -sf -m 2 "${SDK_URL}/health" >/dev/null 2>&1; then
    echo "[SDK] Starting agent_sdk.server for Grok on ${SDK_PORT}..."
    nohup python -m roboco.agent_sdk.server > /tmp/sdk-server.log 2>&1 &
    SDK_PID=$!
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if curl -sf -m 2 "${SDK_URL}/health" >/dev/null 2>&1; then
            echo "[SDK] Ready (PID: ${SDK_PID})"
            break
        fi
        sleep 0.5
    done
    if ! curl -sf -m 2 "${SDK_URL}/health" >/dev/null 2>&1; then
        echo "[SDK] Still starting in background (PID: ${SDK_PID}, see /tmp/sdk-server.log)" >&2
    fi
fi

# Reset budget/terminal counters at start of session (parity with hook).
curl -sf -m 2 -X POST "${SDK_URL}/budget/reset" >/dev/null 2>&1 || true

# --- Briefing + PreCompact recovery (replicate sdk-startup-hook effects) ---
# For Grok one-shot (bypasses Claude SessionStart), cat briefing and any
# precompact recovery file so task context + ACs are visible in logs and
# any stdout capture (parity for post-mortems / debug; system prompt carries
# the role blueprint).
AGENT_ID="${ROBOCO_AGENT_ID:-unknown}"
PRECOMPACT_FILE="/tmp/roboco-precompact-${AGENT_ID}.md"
BRIEFING_FILE="/app/briefing.md"
if [[ -s "$PRECOMPACT_FILE" ]]; then
    echo "### Resumed from compact"
    cat "$PRECOMPACT_FILE"
    echo
fi
if [[ -s "$BRIEFING_FILE" ]]; then
    cat "$BRIEFING_FILE"
fi

# Render ~/.grok/config.toml (the MCP gateway) + the per-role grok flags. Run
# from /app so `python -m` resolves the INSTALLED roboco package: dev/doc/qa
# agents run at their workspace-clone cwd, whose own roboco/ dir would shadow it
# on the sys.path front (the ModuleNotFound lesson). The render reads
# ROBOCO_MCP_CONFIG + ROBOCO_AGENT_ID and writes the config + an args file.
( cd /app && python -m roboco.llm.providers.grok_cli_config )

GROK_ARGS_FILE="${ROBOCO_GROK_ARGS_FILE:-/tmp/roboco-grok-args}"
mapfile -t GROK_ARGS < "$GROK_ARGS_FILE"

# Prompt-injection guard (parity with the Claude UserPromptSubmit hook): the task
# prompt is DATA, not instructions — refuse a poisoned one before the model sees
# it. Same patterns as docker/scripts/user-prompt-hook.sh; run from /app too.
if ! ( cd /app && python -m roboco.agent_sdk.prompt_guard "${ROBOCO_INITIAL_PROMPT:-}" ); then
  echo "Refusing to run: task prompt matched a prompt-injection pattern." >&2
  exit 1
fi

# Run the agent. The prompt comes from an env var (never an untrusted argv
# positional). `< /dev/null` keeps the headless run from blocking on stdin. We
# do NOT `exec`: the script regains control to inspect the result + exit code.
# `--cwd` is the agent's workspace (the orchestrator sets the container workdir
# to it, mirroring the Claude path). Per-role flags (tool removal / deny rules /
# effort / turn cap) come from the rendered args file.
RUN_LOG="/tmp/grok-run.json"
ERR_LOG="/tmp/grok-run.err"
WORKSPACE="${ROBOCO_WORKSPACE:-$PWD}"
# The role blueprint reaches grok as its system prompt via ~/.grok/AGENTS.md (a
# global instruction file grok loads regardless of --cwd, verified live — the
# `--system-prompt-override`/`--rules` flags are ignored in headless mode). The
# render step above (grok_cli_config) wrote it from the mounted system prompt.
# NOTE: grok generates its own session id and ignores a requested one (`-s` does
# not pin it), so we do NOT pass a session id in; usage capture below reads the
# real id back out of the JSON run log instead.
set +e
grok -p "${ROBOCO_INITIAL_PROMPT:-}" \
  -m "${ROBOCO_AGENT_MODEL:-grok-build}" \
  --cwd "$WORKSPACE" \
  --output-format json \
  "${GROK_ARGS[@]}" \
  < /dev/null > "$RUN_LOG" 2> "$ERR_LOG"
run_rc=$?
set -e
# Surface the run output + any stderr into the agent log.
cat "$RUN_LOG"
[ -s "$ERR_LOG" ] && cat "$ERR_LOG" >&2

# Capture token usage from the grok session store (~/.grok/sessions). The reader
# reads the run's real session id out of $ROBOCO_GROK_RUN_LOG, locates the store,
# and writes a usage.json the orchestrator reads back at finalize — the grok
# analogue of the Claude transcript. Best-effort; never fails the run. Run from
# /app for the same module-resolution reason as the render above.
( cd /app && ROBOCO_GROK_RUN_CWD="$WORKSPACE" ROBOCO_GROK_RUN_LOG="$RUN_LOG" \
    python -m roboco.llm.providers.grok_cli_usage ) || true

# Rate-limit detection: an xAI 429 / quota error ends the run without a terminal
# verb. Detect it from the run output and exit 75 (EX_TEMPFAIL) so the
# orchestrator PARKS the grok provider instead of the dispatcher respawning the
# same task every tick (429 -> exit -> respawn, a token loop). A rate-limited task
# is retried once the limit lifts, not dropped.
if grep -qiE '(\b429\b|rate.?limit|too many requests|quota|insufficient_quota)' \
    "$RUN_LOG" "$ERR_LOG" 2>/dev/null; then
  echo "[grok] rate-limited — exiting 75 so the orchestrator parks the provider;" \
    "the task is retried when the limit lifts." >&2
  exit 75
fi

# A graceful exit without a terminal verb is handled server-side by the
# orchestrator (_handle_stopped_container substitutes the still-owned task) —
# the grok-cli runtime needs no in-container SDK server for that.
exit "$run_rc"
