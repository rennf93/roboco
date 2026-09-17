#!/usr/bin/env bash
# Entrypoint for the roboco-agent-codex image (one-shot delivery roles only —
# see docker/agent-codex.Dockerfile for the V1 scope note).
#
# Runs an agent on OpenAI's official `codex` CLI, authenticated by a ChatGPT
# subscription via a mounted ~/.codex/auth.json — the parity analogue of the
# grok-cli entrypoint's mounted ~/.grok. The gateway, identity, and workspace
# are mounted by the orchestrator's shared container assembly (the same that
# wires Claude/grok); this entrypoint renders the codex runtime config from
# that mount and runs the CLI headless.
set -euo pipefail

# Render ~/.codex/config.toml (the MCP gateway) + the execpolicy deny rules +
# the combined system+task prompt + the per-role sandbox flag. Run from /app so
# `python -m` resolves the INSTALLED roboco package: dev/doc/qa agents run at
# their workspace-clone cwd, whose own roboco/ dir would shadow it on the
# sys.path front (the same ModuleNotFound lesson the grok entrypoint documents).
( cd /app && python -m roboco.llm.providers.codex_cli_config )

CODEX_ARGS_FILE="${ROBOCO_CODEX_ARGS_FILE:-/tmp/roboco-codex-args}"
mapfile -t CODEX_ARGS < "$CODEX_ARGS_FILE"

CODEX_PROMPT_FILE="${ROBOCO_CODEX_PROMPT_FILE:-/tmp/roboco-codex-prompt.txt}"

# Prompt-injection guard (parity with the Claude/grok path): the task prompt is
# DATA, not instructions — refuse a poisoned one before the model ever sees the
# combined prompt file the render step above wrote. Screens the RAW task
# prompt only (the composed role blueprint folded into that file is already
# trusted), same scope as the grok guard call. Run from /app too.
if ! ( cd /app && python -m roboco.agent_sdk.prompt_guard "${ROBOCO_INITIAL_PROMPT:-}" ); then
  echo "Refusing to run: task prompt matched a prompt-injection pattern." >&2
  exit 1
fi

# Auth fail-fast guard. The Codex CLI self-refreshes the access token in-process
# when it notices the JWT is within 5 minutes of expiry, but our per-agent mount
# is read-only, so that in-container write silently fails and only the
# in-memory token survives for this one run. The orchestrator refreshes the
# host token on a loop; this is the in-container backstop: exit 78 (EX_CONFIG)
# immediately so _handle_stopped_container surfaces it, instead of the CLI
# hanging or failing deep into the run on an expired credential.
#
# The orchestrator mounts the host ~/.codex DIRECTORY read-only at
# /home/agent/.codex-auth-ro (a single-file bind mount would pin the inode, so
# the atomic auth.json refresh would never reach a running container — same
# concern the grok entrypoint documents). Symlink ~/.codex/auth.json at that RO
# mount so codex + the --check backstop read the LIVE credential, while
# codex's own writable state (config.toml, rules/, sessions/) still lands in
# the image's own ~/.codex. `rm -f` first in case the image baked a stub.
rm -f /home/agent/.codex/auth.json
ln -s /home/agent/.codex-auth-ro/auth.json /home/agent/.codex/auth.json
if ! ( cd /app && python -m roboco.llm.providers.codex_auth --check ); then
  echo "[codex] auth token missing or expired — refusing to run. Refresh" \
    "~/.codex/auth.json (orchestrator auto-refresh or 'codex login' on the" \
    "host)." >&2
  exit 78
fi

# Run the agent. `< /dev/null` keeps the headless run from blocking on stdin.
# We do NOT `exec`: the script regains control to inspect the result + exit
# code. The container's cwd is already the agent's workspace (the orchestrator
# sets it via docker run -w, mirroring the Claude/grok path) — no --cwd flag is
# passed. The combined system+task prompt (from the render step above) is read
# via command substitution into a single quoted argv token, never re-parsed by
# the shell — the same injection-safety property as the grok path's env-var
# prompt passing.
RUN_LOG="/tmp/codex-run.jsonl"
ERR_LOG="/tmp/codex-run.err"
COMBINED_PROMPT="$(cat "$CODEX_PROMPT_FILE" 2>/dev/null || true)"

# `--json` streams typed JSONL to stdout; `tee` shows it live via `docker logs`
# (parity with the Claude/grok path's live streaming) while ALSO capturing it
# to RUN_LOG for the usage-capture read below. stderr goes to ERR_LOG and is
# surfaced after the run.
set +e
codex exec "$COMBINED_PROMPT" \
  -m "${ROBOCO_AGENT_MODEL:-gpt-5.3-codex}" \
  --json \
  "${CODEX_ARGS[@]}" \
  < /dev/null 2> "$ERR_LOG" | tee "$RUN_LOG"
run_rc=${PIPESTATUS[0]}
set -e
[ -s "$ERR_LOG" ] && cat "$ERR_LOG" >&2

# Capture token usage from the run's own captured JSONL (turn.completed.usage
# carries a real input/output/cache split — see codex_cli_usage). Best-effort;
# never fails the run. Run from /app for the same module-resolution reason as
# the render above.
( cd /app && ROBOCO_CODEX_RUN_LOG="$RUN_LOG" \
    python -m roboco.llm.providers.codex_cli_usage ) || true

# Codex has NO exit-code taxonomy — every failure exits 1, so a rate-limit or
# an expired-mid-run auth failure looks identical to any other error at the
# process level. Classify the run WITHOUT scanning the full transcript: the
# model's own on-topic prose can false-positive a raw grep by construction
# (this repo's own role prompts use the phrase "quota-limited"; a commit hash
# or item content can contain "429"; the panel has a literal login page) —
# codex_cli_sniff extracts ONLY the JSONL error.message fields (turn.failed /
# any error-bearing event) plus stderr and classifies THAT, never stdout's
# echoed model output. Mirrors the grok entrypoint's exit-75/78 convention so
# the orchestrator's existing park-and-probe logic, scoped by provider_type,
# handles both providers identically:
#   - rate-limit/quota -> exit 75 (EX_TEMPFAIL): the orchestrator PARKS the
#     provider instead of the dispatcher respawning the same task every tick.
#   - auth failure (an expired/rotated token discovered mid-run, past the
#     --check backstop above) -> exit 78 (EX_CONFIG): parked the same way as a
#     pre-run auth miss.
SNIFF="$( (cd /app && python -m roboco.llm.providers.codex_cli_sniff "$RUN_LOG" "$ERR_LOG") 2>/dev/null || true)"
if [ "$SNIFF" = "rate_limit" ]; then
  echo "[codex] rate-limited — exiting 75 so the orchestrator parks the" \
    "provider; the task is retried when the limit lifts." >&2
  exit 75
fi
if [ "$SNIFF" = "auth" ]; then
  echo "[codex] auth failure detected mid-run — exiting 78 so the" \
    "orchestrator parks the provider until the token is refreshed." >&2
  exit 78
fi

# A graceful exit without a terminal verb is handled server-side by the
# orchestrator (_handle_stopped_container substitutes the still-owned task) —
# the codex-cli runtime needs no in-container SDK server for that.
exit "$run_rc"
