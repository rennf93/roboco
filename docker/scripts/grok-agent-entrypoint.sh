#!/usr/bin/env bash
# Entrypoint for the roboco-agent-grok image (one-shot delivery roles).
#
# Renders opencode.json from the RoboCo spawn env (OPENAI_* + ROBOCO_*, set by
# GrokProvider) plus the mounted Claude Code mcp-config.json, starts the
# in-container SDK server (parity with the Claude SessionStart sdk-startup-hook),
# then runs opencode non-interactively. opencode speaks the OpenAI protocol, so
# grok-build-0.1 runs natively against api.x.ai/v1 with no shim, while still
# reaching the RoboCo MCP gateway (roboco-flow / roboco-do / ...) translated into
# opencode's mcp config.
set -euo pipefail

SDK_PORT="${ROBOCO_SDK_PORT:-9000}"
SDK_URL="http://localhost:${SDK_PORT}"

# Generate opencode.json (provider + model + MCP gateway + permissions +
# instructions). Writes to opencode's global config dir by default.
# Run from /app so `python -m` resolves the INSTALLED roboco package. Dev/doc/qa
# agents run at their workspace-clone cwd, which has its own `roboco/` dir on the
# sys.path front (python -m prepends cwd); on a branch without the grok code that
# clone lacks roboco.llm.providers and shadows /app → ModuleNotFoundError. The
# config render has no cwd dependency (writes global, reads ROBOCO_MCP_CONFIG).
( cd /app && python -m roboco.llm.providers.opencode_config )

# --- SDK server bring-up (Claude-parity) ----------------------------------
# The flow/do MCP servers POST /verb/attempted here for the per-verb circuit
# breaker; the budget-feed opencode plugin POSTs /budget/* + /terminal/* here;
# the post-exit hook below reads /terminal/status and writes the post-mortem.
# Bare `python` (the baked venv) — NOT `uv run`, which would re-sync the clone's
# drifted lock and stall (the #179 fix the Claude hook needs `--no-sync` for).
if ! curl -sf -m 2 "${SDK_URL}/health" >/dev/null 2>&1; then
  nohup python -m roboco.agent_sdk.server >/tmp/sdk-server.log 2>&1 &
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf -m 2 "${SDK_URL}/health" >/dev/null 2>&1; then break; fi
    sleep 0.5
  done
fi
# Zero the budget/terminal counters at the start of the session.
curl -sf -m 2 -X POST "${SDK_URL}/budget/reset" >/dev/null 2>&1 || true

# This is a one-shot delivery agent: the SDK budget server above is mandatory.
# Tell the budget-feed plugin to FAIL CLOSED if that server ever goes
# unreachable mid-run, so an unenforceable cost cap halts the burn instead of
# letting it run uncapped. (Interactive serve images set no such flag.)
export ROBOCO_BUDGET_ENFORCE=1

# Prompt-injection guard (parity with the Claude UserPromptSubmit hook): the
# task prompt is DATA, not instructions — refuse a poisoned one before it
# reaches the model. Same patterns as docker/scripts/user-prompt-hook.sh.
if ! python -m roboco.agent_sdk.prompt_guard "${ROBOCO_INITIAL_PROMPT:-}"; then
  echo "Refusing to run: task prompt matched a prompt-injection pattern." >&2
  exit 1
fi

# Reasoning effort: GrokProvider sets ROBOCO_GROK_VARIANT per role (e.g.
# "minimal" for coordination/docs roles to cut reasoning cost). Absent =
# opencode default (full reasoning).
variant_arg=()
if [ -n "${ROBOCO_GROK_VARIANT:-}" ]; then
  variant_arg=(--variant "$ROBOCO_GROK_VARIANT")
fi

# Run the agent. The prompt comes from an env var (never an untrusted argv
# positional); `--` separates it from flags so a prompt starting with `--`
# cannot be parsed as CLI options. `< /dev/null` is REQUIRED: without a closed
# stdin, `opencode run` hangs after init in a headless / no-TTY environment.
#
# We do NOT `exec`: the script must regain control after opencode exits to run
# the post-mortem + silent-exit substitute below (the Claude SessionEnd / Stop
# hooks have no opencode equivalent, so the boundary handles them). `set +e`
# around the run so a non-zero opencode exit doesn't abort before the post-run
# hooks; tee captures the output for rate-limit detection and PIPESTATUS
# preserves opencode's real exit code through the pipe.
RUN_LOG="/tmp/opencode-run.log"
set +e
opencode run \
  --model "xai/${ROBOCO_AGENT_MODEL:-grok-build-0.1}" \
  "${variant_arg[@]}" \
  -- "${ROBOCO_INITIAL_PROMPT:-}" < /dev/null 2>&1 | tee "$RUN_LOG"
run_rc=${PIPESTATUS[0]}
set -e

# --- Rate-limit detection (B4) ---------------------------------------------
# A 429 from xAI ends the one-shot run without the agent ever calling a terminal
# verb. Detect it from the run output and exit 75 (EX_TEMPFAIL) so the
# orchestrator PARKS the grok provider instead of the dispatcher re-spawning the
# same task every tick (429 -> exit -> respawn -> 429, a cost/token loop). A
# rate-limited task is NOT substituted — it must be retried once the limit lifts.
RATE_LIMITED=0
if grep -qiE '(\b429\b|too many requests|rate.?limit|quota exceeded|rate_limit_exceeded)' \
    "$RUN_LOG" 2>/dev/null; then
  RATE_LIMITED=1
fi

# --- Post-mortem (Claude SessionEnd parity) — always -----------------------
terminal=$(curl -sf -m 2 "${SDK_URL}/terminal/status" 2>/dev/null || echo "")
last_tool="null"
had_terminal="false"
if [ -n "$terminal" ]; then
  last_tool=$(echo "$terminal" | jq -r '.last_tool // "null"' 2>/dev/null || echo "null")
  had_terminal=$(echo "$terminal" | jq -r '.had_terminal_recently // false' 2>/dev/null || echo "false")
fi

curl -sf -m 3 -X POST "${SDK_URL}/journal/post_mortem" \
  -H "Content-Type: application/json" \
  -d "{\"terminal_tool\":\"${last_tool}\",\"reason\":\"session_end\"}" \
  >/dev/null 2>&1 || true

if [ "$RATE_LIMITED" = "1" ]; then
  echo "[grok] xAI rate-limited — exiting 75 so the orchestrator parks the" \
    "provider; the task is retried when the limit lifts (not substituted)." >&2
  exit 75
fi

# --- Silent-exit substitute (Claude Stop parity) ---------------------------
# Only when NOT rate-limited: if the agent exited WITHOUT a terminal verb
# (i_am_idle / i_am_done / pass / fail / ...), auto-substitute the task so it is
# not left stuck in claimed/in_progress for a human to hand-unstick.
if [ "$had_terminal" != "true" ]; then
  curl -sf -m 3 -X POST "${SDK_URL}/terminal/force_substitute" >/dev/null 2>&1 || true
  echo "[grok] exited without a terminal verb (last tool: ${last_tool}) — auto-substituted." >&2
fi

exit "$run_rc"
