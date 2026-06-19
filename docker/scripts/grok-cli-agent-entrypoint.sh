#!/usr/bin/env bash
# Entrypoint for the roboco-agent-grok-cli image (one-shot delivery roles).
#
# Runs an agent on xAI's official `grok` CLI (Grok Build), authenticated by the
# SuperGrok subscription via a mounted ~/.grok/auth.json — the parity analogue of
# the Claude Code path's mounted ~/.claude. The gateway, identity, and workspace
# are mounted by the orchestrator's shared container assembly (the same that
# wires Claude); this entrypoint renders the grok runtime config from that mount
# and runs the CLI headless.
set -euo pipefail

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

# Rate-limit detection (parity with the opencode B4 path): an xAI 429 / quota
# error ends the run without a terminal verb. Detect it from the run output and
# exit 75 (EX_TEMPFAIL) so the orchestrator PARKS the grok provider instead of
# the dispatcher respawning the same task every tick (429 -> exit -> respawn, a
# token loop). A rate-limited task is retried once the limit lifts, not dropped.
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
