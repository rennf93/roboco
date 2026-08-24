#!/usr/bin/env bash
# Entrypoint for the roboco-agent-openrouter image (one-shot delivery roles
# only — see docker/agent-openrouter.Dockerfile for the V1 scope note).
#
# Runs an agent on any OpenRouter model through the official `opencode` CLI
# (sst/opencode), authenticated by a static metered API key injected via env
# (OPENROUTER_API_KEY + OPENROUTER_BASE_URL) — the Ollama shape, NOT the
# subscription-mount shape of kimi/codex/gemini/grok. There is NO ~/. auth
# mount to symlink and NO _auth.py refresh loop: the key is a plain env var
# the provider injects at spawn (roboco.llm.providers.openrouter), so this
# entrypoint skips the symlink phase entirely and the auth preflight is just
# "is OPENROUTER_API_KEY non-empty".
#
# The gateway, identity, and workspace are mounted by the orchestrator's
# shared container assembly (the same that wires Claude/grok/kimi); this
# entrypoint renders the opencode runtime config (opencode.json: agent
# prompt + permission.bash deny-rules + mcp + the OpenRouter provider block)
# from that mount and runs the CLI headless. opencode has NO PreToolUse hook
# system (confirmed by the spike — see the decision journal note), so there
# is NO bash-guard wrapper script here: the permission.bash deny-rules in the
# rendered opencode.json ARE the bash-guard (roboco.llm.providers.openrouter_cli_config).
set -euo pipefail

# Split-install sanity: docker/agent-openrouter.Dockerfile installs the
# binary via `npm install -g opencode-ai` — verify it resolved on PATH
# before doing any other work.
command -v opencode >/dev/null || {
  echo "[openrouter] opencode CLI not found on PATH — image build is broken." >&2
  exit 1
}

# Render ~/.config/opencode/opencode.json (the `roboco` agent block with the
# mounted system prompt + per-role permission.bash deny-rules + the mounted
# mcp-config.json passthrough + the OpenRouter provider block pointing at
# OPENROUTER_BASE_URL). Run from /app so `python -m` resolves the INSTALLED
# roboco package: dev/doc/qa agents run at their workspace-clone cwd, whose
# own roboco/ dir would shadow it on the sys.path front (the same
# ModuleNotFound lesson the kimi/codex/grok entrypoints document).
( cd /app && python -m roboco.llm.providers.openrouter_cli_config )

# Prompt-injection guard (parity with the Claude/grok/kimi path): the task
# prompt is DATA, not instructions — refuse a poisoned one before the model
# ever sees it. The composed role blueprint travels separately via the
# agent block's `prompt` field (rendered above), so only the raw task prompt
# is screened here. Run from /app too.
if ! ( cd /app && python -m roboco.agent_sdk.prompt_guard "${ROBOCO_INITIAL_PROMPT:-}" ); then
  echo "Refusing to run: task prompt matched a prompt-injection pattern." >&2
  exit 1
fi

# Auth fail-fast guard. OpenRouter uses a static metered API key (the Ollama
# shape) — no ~/. mount, no refresh token, no expiry to decode. The preflight
# is just "is OPENROUTER_API_KEY set and non-empty?"; openrouter_cli_config
# --check reads it (and OPENROUTER_BASE_URL) and refuses fast (exit 78 /
# EX_CONFIG) on a missing key, instead of the CLI failing deep into the run.
if ! ( cd /app && python -m roboco.llm.providers.openrouter_cli_config --check ); then
  echo "[openrouter] OPENROUTER_API_KEY missing or empty — refusing to run." \
    "Set the project's provider_auth_token (the OpenRouter API key) before" \
    "spawning OpenRouter agents." >&2
  exit 78
fi

# Run the agent. `< /dev/null` keeps the headless run from blocking on stdin.
# We do NOT `exec`: the script regains control to classify the exit code +
# capture usage. The container's cwd is already the agent's workspace (the
# orchestrator sets it via docker run -w, mirroring the Claude/grok/kimi
# path). The prompt travels ONLY as an env-var expansion into a single
# quoted argv token (never re-parsed by the shell) — the same
# injection-safety property as the kimi/gemini path's env-var prompt passing;
# the composed role blueprint is NOT folded in here since it already reached
# the model via the agent block's `prompt` field rendered above.
#
# `--format json` streams JSONL to stdout; `tee` shows it live via
# `docker logs` (parity with the kimi/codex path) while ALSO capturing it to
# RUN_LOG for the usage-capture + sniff reads below. Never pipe this through
# `head` (a known EPIPE hazard on an early-closing reader — `tee` alone is
# safe, it always drains stdin to completion). stderr goes to ERR_LOG and is
# surfaced after the run. `--auto` is opencode's headless auto-approval
# (parity with grok's --dangerously-skip-permissions / codex's --sandbox
# workspace-write + full-auto); `--agent roboco` selects the rendered agent
# block; `--model` is the OpenRouter model id (provider/model, e.g.
# "anthropic/claude-sonnet-4") the orchestrator passes via ROBOCO_AGENT_MODEL.
RUN_LOG="/tmp/openrouter-run.jsonl"
ERR_LOG="/tmp/openrouter-run.err"

set +e
opencode run "${ROBOCO_INITIAL_PROMPT:-}" \
  --agent roboco \
  --model "${ROBOCO_AGENT_MODEL:-anthropic/claude-sonnet-4}" \
  --auto \
  --format json \
  < /dev/null 2> "$ERR_LOG" | tee "$RUN_LOG"
run_rc=${PIPESTATUS[0]}
set -e
[ -s "$ERR_LOG" ] && cat "$ERR_LOG" >&2

# Capture token usage + cost from the run log. Unlike kimi (which reports
# nothing usage-shaped in stdout and requires a session-dir wire.jsonl
# lookup), opencode puts the usage inline in each `step_finish` event's
# `part.tokens` + `part.cost` — so the usage reader just sums the RUN_LOG.
# The cost is OpenRouter's metered `cost` field, NOT the static _PRICING
# table (no OpenRouter rows by design — see openrouter_cli_usage). Best
# effort; never fails the run. Run from /app for the same module-resolution
# reason as the render above.
( cd /app && ROBOCO_OPENROUTER_RUN_LOG="$RUN_LOG" \
    python -m roboco.llm.providers.openrouter_cli_usage ) || true

# opencode has NO documented exit-code taxonomy for `opencode run` (every
# failure looks the same at the process level). Classify the run WITHOUT
# scanning the full transcript: the model's own on-topic prose can
# false-positive a raw grep by construction — openrouter_cli_sniff extracts
# ONLY structured `type: "error"` JSONL event fields plus stderr and
# classifies THAT, never stdout's echoed assistant/tool content. Mirrors
# the kimi/codex/grok/gemini entrypoints' exit-75/78 convention so the
# orchestrator's existing park-and-probe logic, scoped by provider_type,
# handles all five providers identically:
#   - rate-limit/quota -> exit 75 (EX_TEMPFAIL): the orchestrator PARKS the
#     provider instead of the dispatcher respawning the same task every tick.
#   - auth/key failure (an invalid or revoked key discovered mid-run, past
#     the --check backstop above) -> exit 78 (EX_CONFIG): parked the same way
#     as a pre-run auth miss.
SNIFF="$( (cd /app && python -m roboco.llm.providers.openrouter_cli_sniff "$RUN_LOG" "$ERR_LOG") 2>/dev/null || true)"
if [ "$SNIFF" = "rate_limit" ]; then
  echo "[openrouter] rate-limited — exiting 75 so the orchestrator parks the" \
    "provider; the task is retried when the limit lifts." >&2
  exit 75
fi
if [ "$SNIFF" = "auth" ]; then
  echo "[openrouter] auth/key failure detected mid-run — exiting 78 so the" \
    "orchestrator parks the provider until the credential is refreshed." >&2
  exit 78
fi

# A graceful exit without a terminal verb is handled server-side by the
# orchestrator (_handle_stopped_container substitutes the still-owned task) —
# the opencode runtime needs no in-container SDK server for that.
exit "$run_rc"