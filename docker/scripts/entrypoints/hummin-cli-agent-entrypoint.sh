#!/usr/bin/env bash
# Entrypoint for the roboco-agent-hummin image (one-shot delivery roles only —
# see docker/agent-hummin.Dockerfile for the V1 scope note).
#
# Runs an agent on the GLM-native `hummin` CLI (a pi-harness fork),
# authenticated by the operator's Z.ai key for the GLM Coding Plan injected
# as ZAI_API_KEY (the OpenRouter shape — no credential mount; see
# roboco.llm.providers.hummin). The gateway, identity, and workspace are
# mounted by the orchestrator's shared container assembly; this entrypoint
# renders the hummin runtime config, runs the auth preflight, and runs the
# CLI headless.
#
# V1 contract notes (verified against the hummin source — see
# roboco.llm.providers.hummin_cli_config):
#   - `--no-extensions` is REQUIRED: without it hummin's bundled colibri
#     extension probes localhost ports at startup. It also means NO
#     extension/custom tools and NO MCP bridge exist in V1 — the built-in
#     tool surface is scoped per role via the rendered `--tools` allowlist.
#   - stdin must be closed (`< /dev/null`) or startup blocks reading it.
#   - stdout carries ONLY protocol JSONL (tee'd live to docker logs and
#     captured for usage + sniff reads below).
#   - JSON mode exits 0 EVEN WHEN the assistant errored or aborted — the
#     sniff classification below decides rate-limit/auth parks, never the
#     raw exit code alone.
set -euo pipefail

command -v hummin >/dev/null || {
  echo "[hummin] hummin CLI not found on PATH — image build is broken." >&2
  exit 1
}

# Render ~/.hummin/agent/settings.json (fleet-safe defaults, merged-
# preserving) + /tmp/roboco-hummin-env (the per-role --tools allowlist).
# Run from /app so `python -m` resolves the INSTALLED roboco package: dev/
# doc/qa agents run at their workspace-clone cwd, whose own roboco/ dir
# would shadow it on the sys.path front (the same ModuleNotFound lesson the
# codex/grok/kimi entrypoints document).
( cd /app && python -m roboco.llm.providers.hummin_cli_config )

# shellcheck disable=SC1091
source /tmp/roboco-hummin-env

# Prompt-injection guard (parity with the Claude/grok/codex/kimi path): the
# task prompt is DATA, not instructions — refuse a poisoned one before the
# model ever sees it. The composed role blueprint travels separately via
# --system-prompt below, so only the raw task prompt is screened here.
if ! ( cd /app && python -m roboco.agent_sdk.prompt_guard "${ROBOCO_INITIAL_PROMPT:-}" ); then
  echo "Refusing to run: task prompt matched a prompt-injection pattern." >&2
  exit 1
fi

# Auth fail-fast guard. Auth is a static env key (ZAI_API_KEY, the GLM
# Coding Plan credential) — no mount, no refresh loop. hummin's own
# preflight (`hummin auth check --provider zai --json`) exits 0 ready /
# 1 not_ready / 2 invalid; ANY non-zero maps to 78 (EX_CONFIG) so the
# orchestrator parks the provider until the operator sets the key
# (PUT /providers/hummin-key).
AUTH_RC=0
( cd /app && python -m roboco.llm.providers.hummin_cli_config --check ) || AUTH_RC=$?
if [ "$AUTH_RC" -ne 0 ]; then
  echo "[hummin] auth preflight failed (status ${AUTH_RC}) — refusing to run. Set" \
    "the Z.ai key via PUT /providers/hummin-key (or export ZAI_API_KEY for" \
    "the orchestrator) before spawning hummin agents." >&2
  exit 78
fi

# Run the agent. `< /dev/null` keeps the headless run from blocking on
# stdin. We do NOT `exec`: the script regains control to sniff the JSONL +
# capture usage. The container's cwd is already the agent's workspace (the
# orchestrator sets it via docker run -w). The prompt travels only as an
# env-var expansion into a single quoted argv token (never re-parsed by the
# shell) — the same injection-safety property as the kimi/grok path.
# `--mode json` streams protocol JSONL to stdout; `tee` shows it live via
# `docker logs` while ALSO capturing it to RUN_LOG for the usage + sniff
# reads below (never pipe through `head` — EPIPE hazard; `tee` alone is
# safe). stderr goes to ERR_LOG and is surfaced after the run.
SYSTEM_PROMPT_FILE="${ROBOCO_SYSTEM_PROMPT:-/app/system-prompt.md}"
RUN_LOG="/tmp/hummin-run.jsonl"
ERR_LOG="/tmp/hummin-run.err"

set +e
if [ -s "$SYSTEM_PROMPT_FILE" ]; then
  hummin --mode json \
    --model "zai/${ROBOCO_AGENT_MODEL:-glm-5.3-flash:high}" \
    --system-prompt "$(cat "$SYSTEM_PROMPT_FILE")" \
    ${ROBOCO_HUMMIN_TOOLS:+--tools "$ROBOCO_HUMMIN_TOOLS"} \
    --no-extensions \
    -p "${ROBOCO_INITIAL_PROMPT:-}" \
    < /dev/null 2> "$ERR_LOG" | tee "$RUN_LOG"
else
  echo "[hummin] system prompt file missing at ${SYSTEM_PROMPT_FILE} — running on the CLI default prompt." >&2
  hummin --mode json \
    --model "zai/${ROBOCO_AGENT_MODEL:-glm-5.3-flash:high}" \
    ${ROBOCO_HUMMIN_TOOLS:+--tools "$ROBOCO_HUMMIN_TOOLS"} \
    --no-extensions \
    -p "${ROBOCO_INITIAL_PROMPT:-}" \
    < /dev/null 2> "$ERR_LOG" | tee "$RUN_LOG"
fi
run_rc=${PIPESTATUS[0]}
set -e
[ -s "$ERR_LOG" ] && cat "$ERR_LOG" >&2

# Capture token usage from the tee'd `--mode json` run log (sum of
# message.usage across ALL assistant message_end events — see
# hummin_cli_usage). Best-effort; never fails the run.
( cd /app && ROBOCO_HUMMIN_RUN_LOG="$RUN_LOG" \
    python -m roboco.llm.providers.hummin_cli_usage ) || true

# JSON mode exits 0 even on an assistant error, so classify WITHOUT
# scanning the transcript: hummin_cli_sniff extracts ONLY structured error
# channels (assistant stopReason/errorMessage + event-level error.message)
# plus raw stderr — the model's own echoed content can never reach the
# classifier. Mirrors the kimi/codex/grok/gemini entrypoints' exit-75/78
# convention so the orchestrator's park-and-probe logic, scoped by
# provider_type, handles every CLI provider identically:
#   - Z.ai rate-limit/quota -> exit 75 (EX_TEMPFAIL): the orchestrator
#     PARKS the provider instead of respawning the same task every tick.
#   - auth failure (a 401/403/invalid key discovered mid-run, past the
#     preflight above) -> exit 78 (EX_CONFIG): parked the same way as a
#     pre-run auth miss.
SNIFF="$( (cd /app && python -m roboco.llm.providers.hummin_cli_sniff "$RUN_LOG" "$ERR_LOG") 2>/dev/null || true)"
if [ "$SNIFF" = "rate_limit" ]; then
  echo "[hummin] rate-limited — exiting 75 so the orchestrator parks the" \
    "provider; the task is retried when the limit lifts." >&2
  exit 75
fi
if [ "$SNIFF" = "auth" ]; then
  echo "[hummin] auth failure detected mid-run — exiting 78 so the" \
    "orchestrator parks the provider until the key is set/fixed." >&2
  exit 78
fi

# A graceful exit without a terminal verb is handled server-side by the
# orchestrator (_handle_stopped_container substitutes the still-owned task)
# — the hummin-cli runtime has no in-container SDK server and, in V1, no
# MCP gateway client at all (see roboco.llm.providers.hummin).
exit "$run_rc"
