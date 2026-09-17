#!/usr/bin/env bash
# Entrypoint for the roboco-agent-kimi image (one-shot delivery roles only —
# see docker/agent-kimi.Dockerfile for the V1 scope note).
#
# Runs an agent on Moonshot's official `kimi` (kimi-code) CLI, authenticated
# by a Kimi subscription via a symlinked-in
# ~/.kimi-code/credentials/kimi-code.json + oauth/ (the shared RW auth mount)
# — the parity analogue of the codex-cli entrypoint's mounted ~/.codex. The
# gateway, identity, and workspace are mounted by the orchestrator's shared
# container assembly (the same that wires Claude/grok/codex); this entrypoint
# symlinks the host credential in, renders the kimi runtime config from that
# mount, and runs the CLI headless.
set -euo pipefail

# Split-install sanity: docker/agent-kimi.Dockerfile installs the binary to
# KIMI_INSTALL_DIR=/usr/local (split from KIMI_CODE_HOME's mutable state) —
# verify it actually resolved on PATH before doing any other work.
command -v kimi >/dev/null || {
  echo "[kimi] kimi CLI not found on PATH — image build is broken." >&2
  exit 1
}

# Symlink phase. The orchestrator mounts the host ~/.kimi-code DIRECTORY
# READ-WRITE at this path (roboco.llm.providers.kimi._append_kimi_auth_mount).
# Moonshot's refresh token is rotation-with-short-reuse-grace, not truly
# reusable (live-verified: a per-container COPY cross-invalidates the shared
# chain once the grace window passes — see roboco.llm.providers.kimi's
# module docstring), so every container must redeem the SAME chain the host
# uses: symlink credentials/ AND oauth/ (the cross-process refresh lock
# directory — entirely missing from the old copy-in, and load-bearing: it's
# what serializes concurrent redemptions across containers + the host)
# straight into the image's own, writable ~/.kimi-code. config.toml/mcp.json/
# AGENTS.md are still rendered fresh below, never symlinked.
AUTH_DIR="/home/agent/.kimi-code-auth"
mkdir -p /home/agent/.kimi-code
if [ -d "$AUTH_DIR/credentials" ]; then
  mkdir -p "$AUTH_DIR/oauth"
  ln -sfn "$AUTH_DIR/credentials" /home/agent/.kimi-code/credentials
  ln -sfn "$AUTH_DIR/oauth" /home/agent/.kimi-code/oauth
fi

# Render ~/.kimi-code/config.toml (managed provider/model blocks + telemetry/
# upgrade knobs + per-role [[permission.rules]] + the bash-guard [[hooks]])
# + mcp.json + AGENTS.md. Run from /app so `python -m` resolves the INSTALLED
# roboco package: dev/doc/qa agents run at their workspace-clone cwd, whose
# own roboco/ dir would shadow it on the sys.path front (the same
# ModuleNotFound lesson the codex/grok entrypoints document).
( cd /app && python -m roboco.llm.providers.kimi_cli_config )

# Prompt-injection guard (parity with the Claude/grok/codex path): the task
# prompt is DATA, not instructions — refuse a poisoned one before the model
# ever sees it. The composed role blueprint travels separately via the
# additive AGENTS.md (rendered above), so only the raw task prompt is
# screened here. Run from /app too.
if ! ( cd /app && python -m roboco.agent_sdk.prompt_guard "${ROBOCO_INITIAL_PROMPT:-}" ); then
  echo "Refusing to run: task prompt matched a prompt-injection pattern." >&2
  exit 1
fi

# Auth fail-fast guard. D2 resolved Kimi's refresh token as
# rotation-with-short-reuse-grace over ONE shared chain (symlinked, not
# copied — see above) — each container self-refreshes through the CLI's own
# cross-process lock, so there is no orchestrator-side refresh daemon to
# backstop, only this preflight: read the symlinked
# credentials/kimi-code.json's expires_at (a plain JSON field, no JWT
# decode) and refuse fast (exit 78 / EX_CONFIG) on missing/expired, instead
# of the CLI hanging or failing deep into the run.
if ! ( cd /app && python -m roboco.llm.providers.kimi_cli_config --check ); then
  echo "[kimi] auth credential missing or expired — refusing to run. Run" \
    "\`kimi login\` on the host (or set ROBOCO_HOST_KIMI_DIR to the" \
    "directory holding credentials/kimi-code.json) before spawning Kimi" \
    "agents." >&2
  exit 78
fi

# Run the agent. `< /dev/null` keeps the headless run from blocking on
# stdin. We do NOT `exec`: the script regains control to classify the exit
# code + capture usage. The container's cwd is already the agent's
# workspace (the orchestrator sets it via docker run -w, mirroring the
# Claude/grok/codex path) — captured here BEFORE the usage-capture step's
# `cd /app` needs it to locate the right sessions/wd_<cwd-basename>_*/ dir.
# The prompt travels only as an env-var expansion into a single quoted argv
# token (never re-parsed by the shell) — the same injection-safety property
# as the grok/gemini path's env-var prompt passing; the composed role
# blueprint is NOT folded in here (unlike codex) since it already reached
# the model via the additive AGENTS.md rendered above.
WORKDIR="$PWD"
RUN_LOG="/tmp/kimi-run.jsonl"
ERR_LOG="/tmp/kimi-run.err"

# `--output-format stream-json` streams JSONL to stdout; `tee` shows it live
# via `docker logs` (parity with the Claude/grok/codex path) while ALSO
# capturing it to RUN_LOG for the usage-capture + sniff reads below. Never
# pipe this through `head` (a known EPIPE hazard on an early-closing reader —
# `tee` alone is safe, it always drains stdin to completion). stderr goes to
# ERR_LOG and is surfaced after the run.
set +e
kimi -p "${ROBOCO_INITIAL_PROMPT:-}" \
  --output-format stream-json \
  -m "${ROBOCO_AGENT_MODEL:-kimi-code/k3}" \
  < /dev/null 2> "$ERR_LOG" | tee "$RUN_LOG"
run_rc=${PIPESTATUS[0]}
set -e
[ -s "$ERR_LOG" ] && cat "$ERR_LOG" >&2

# Capture token usage from the session's wire.jsonl (kimi's stdout carries no
# usage summary of its own, unlike codex/gemini — see kimi_cli_usage for the
# session-dir resolution). Best-effort; never fails the run. Run from /app
# for the same module-resolution reason as the render above; ROBOCO_KIMI_WORKDIR
# carries the captured workspace cwd so the usage reader can find the right
# sessions/wd_<cwd-basename>_*/ directory after this subshell's own `cd /app`.
( cd /app && ROBOCO_KIMI_RUN_LOG="$RUN_LOG" ROBOCO_KIMI_WORKDIR="$WORKDIR" \
    python -m roboco.llm.providers.kimi_cli_usage ) || true

# Kimi has NO documented exit-code taxonomy for `-p` (a claimed 75/1 split is
# unverified noise) — every failure looks the same at the process level.
# Classify the run WITHOUT scanning the full transcript: the model's own
# on-topic prose can false-positive a raw grep by construction — kimi_cli_sniff
# extracts ONLY structured error fields off error-bearing JSONL events plus
# stderr and classifies THAT, never stdout's echoed assistant/tool content.
# Mirrors the codex/grok/gemini entrypoints' exit-75/78 convention so the
# orchestrator's existing park-and-probe logic, scoped by provider_type,
# handles all four providers identically:
#   - rate-limit/quota -> exit 75 (EX_TEMPFAIL): the orchestrator PARKS the
#     provider instead of the dispatcher respawning the same task every tick.
#   - auth/membership failure (a lapsed subscription or an expired credential
#     discovered mid-run, past the --check backstop above) -> exit 78
#     (EX_CONFIG): parked the same way as a pre-run auth miss.
SNIFF="$( (cd /app && python -m roboco.llm.providers.kimi_cli_sniff "$RUN_LOG" "$ERR_LOG") 2>/dev/null || true)"
if [ "$SNIFF" = "rate_limit" ]; then
  echo "[kimi] rate-limited — exiting 75 so the orchestrator parks the" \
    "provider; the task is retried when the limit lifts." >&2
  exit 75
fi
if [ "$SNIFF" = "auth" ]; then
  echo "[kimi] auth/membership failure detected mid-run — exiting 78 so the" \
    "orchestrator parks the provider until the credential is refreshed." >&2
  exit 78
fi

# A graceful exit without a terminal verb is handled server-side by the
# orchestrator (_handle_stopped_container substitutes the still-owned task) —
# the kimi-cli runtime needs no in-container SDK server for that.
exit "$run_rc"
