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

# Auth fail-fast guard. The SuperGrok token (~/.grok/auth.json) has a ~6h TTL;
# on an expired/missing token headless grok does NOT refresh — it hangs forever
# at an interactive "Waiting for authorization..." prompt, which reads as a
# silent zombie container. The orchestrator refreshes the host token on a loop;
# this is the in-container backstop: exit 78 (EX_CONFIG) immediately so
# _handle_stopped_container surfaces it, instead of hanging for hours.
#
# F005: the orchestrator mounts the host ~/.grok DIRECTORY read-only at
# /home/agent/.grok-auth-ro (a single-file bind mount pins the inode, so the
# atomic auth.json refresh never reached a running container). Symlink
# ~/.grok/auth.json at that RO mount so grok + the --check backstop read the
# LIVE credential (the directory mount sees the host-side rename), while grok's
# own writable state (config.toml, sessions/) still lands in the image's
# ~/.grok. `rm -f` first in case the image baked a stub auth.json.
rm -f /home/agent/.grok/auth.json
ln -s /home/agent/.grok-auth-ro/auth.json /home/agent/.grok/auth.json
if ! ( cd /app && python -m roboco.llm.providers.grok_auth --check ); then
  echo "[grok] auth token missing or expired — refusing to run (would hang at" \
    "the login prompt). Refresh ~/.grok/auth.json (orchestrator auto-refresh or" \
    "'grok login' on the host)." >&2
  exit 78
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
# real id back out of the run log instead.
#
# `--output-format streaming-json` + `tee` streams the run to the container's
# stdout LIVE (so `docker logs` shows the agent reasoning/answering in real time,
# parity with the Claude path's stream-json) while ALSO capturing it to RUN_LOG
# for the session-id / usage read below. Without this the run is invisible until
# it ends (the buffered-to-a-file black box). stderr (grok's tool calls /
# diagnostics) goes to ERR_LOG and is surfaced after the run.
set +e
grok -p "${ROBOCO_INITIAL_PROMPT:-}" \
  -m "${ROBOCO_AGENT_MODEL:-grok-build}" \
  --cwd "$WORKSPACE" \
  --output-format streaming-json \
  "${GROK_ARGS[@]}" \
  < /dev/null 2> "$ERR_LOG" | tee "$RUN_LOG"
run_rc=${PIPESTATUS[0]}
set -e
# stdout already streamed live via tee; surface stderr (tool calls / errors) too.
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
