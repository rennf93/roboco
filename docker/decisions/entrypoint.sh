#!/usr/bin/env bash
# roboco-decisions sidecar supervisor.
#
# Starts the llama.cpp decision servers this container serves (PR #29818,
# POST /v1/systemone), waits for each /health, then runs the FastAPI proxy
# (server.py) in the foreground:
#
#   - clef tier: ggml-org/Clef-Flash-GGUF on 127.0.0.1:8110, on the GPU
#     when /dev/dri is passed in (Vulkan), else on the CPU;
#   - laya (cheap option): ggml-org/Laya-GGUF on 127.0.0.1:8111, CPU only,
#     ctx ${LAYA_CTX_SIZE:-4096}.
#
# Either backend can be turned off with CLEF_ENABLED=false /
# LAYA_ENABLED=false (the CI live-contract job ships clef-less; the proxy's
# /health reports the disabled backend honestly). A backend whose GGUF is
# missing or empty is skipped instead of crash-looping the container - the
# proxy's /health then reports that upstream unreachable. A backend that
# starts but never gets healthy (or dies during startup) tears the whole
# supervisor down so compose's restart policy recovers the pair.
# TERM/INT kill every child.
#
# Decision models keep no KV cache: llama-server must take a whole input in
# ONE physical batch and refuses a longer one at once ("input (N tokens) is
# too large to process"). So each backend's batch, micro-batch and context
# equal its input cap (CLEF_MAX_INPUT_TOKENS, LAYA_CTX_SIZE), and the
# proxy's admission control (server.py) never starts an input that cannot
# finish before its caller's deadline: on 2026-11-01 one 10k-token input
# held the CPU for nine minutes and every request queued behind it timed out.
# One slot (--parallel 1): the proxy queues, llama-server serves one input.
# Threads default to every CPU the container sees: llama.cpp's own default
# ran Clef-Flash 2.4x slower on the NAS (6.6 vs 15.1 tok/s, measured).

set -uo pipefail

DECISIONS_PORT="${DECISIONS_PORT:-8100}"
DECISIONS_HOST="${DECISIONS_HOST:-0.0.0.0}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-/usr/local/bin/llama-server}"
LLAMA_THREADS="${LLAMA_THREADS:-$(nproc)}"
LAYA_THREADS="${LAYA_THREADS:-4}"
CLEF_ENABLED="${CLEF_ENABLED:-true}"
LAYA_ENABLED="${LAYA_ENABLED:-true}"
CLEF_UPSTREAM_URL="${CLEF_UPSTREAM_URL:-http://127.0.0.1:8110}"
LAYA_UPSTREAM_URL="${LAYA_UPSTREAM_URL:-http://127.0.0.1:8111}"
CLEF_PORT="${CLEF_PORT:-8110}"
LAYA_PORT="${LAYA_PORT:-8111}"
CLEF_MAX_INPUT_TOKENS="${CLEF_MAX_INPUT_TOKENS:-8192}"
LAYA_CTX_SIZE="${LAYA_CTX_SIZE:-4096}"
CLEF_GGUF_PATH="${CLEF_GGUF_PATH:-/models/Clef-Flash-Q4_K_M.gguf}"
LAYA_GGUF_PATH="${LAYA_GGUF_PATH:-/models/Laya-Q8_0.gguf}"
CLEF_MODEL_ID="${CLEF_MODEL_ID:-ggml-org/Clef-Flash-GGUF:Q4_K_M}"
LAYA_MODEL_ID="${LAYA_MODEL_ID:-ggml-org/Laya-GGUF}"
BACKEND_START_TIMEOUT_S="${BACKEND_START_TIMEOUT_S:-600}"

PIDS=()
LAST_PID=""

log() { echo "[entrypoint] $*"; }

cleanup() {
  trap - TERM INT
  log "shutting down: ${#PIDS[@]} child process(es)"
  local pid
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup TERM INT

port_of() {
  case "$1" in
    clef) echo "$CLEF_PORT" ;;
    *) echo "$LAYA_PORT" ;;
  esac
}

gguf_of() {
  case "$1" in
    clef) echo "$CLEF_GGUF_PATH" ;;
    *) echo "$LAYA_GGUF_PATH" ;;
  esac
}

alias_of() {
  case "$1" in
    clef) echo "$CLEF_MODEL_ID" ;;
    *) echo "$LAYA_MODEL_ID" ;;
  esac
}

ctx_of() {
  case "$1" in
    clef) echo "$CLEF_MAX_INPUT_TOKENS" ;;
    *) echo "$LAYA_CTX_SIZE" ;;
  esac
}

# Both backends offload every layer to the GPU when one is present (with
# none, the Vulkan backend reports "No devices found" and the CPU serves
# them). Laya is 421M and thread-bound on the CPU: measured on the NAS
# 2026-11-01, one call took 0.3 s on the Iris Xe, 2.5 s on 4 CPU threads
# and 5.4 s on 12, so it runs on LAYA_THREADS (default 4) and Clef-Flash on
# every CPU the container sees.
gpu_layers_of() {
  echo 99
}

threads_of() {
  case "$1" in
    clef) echo "$LLAMA_THREADS" ;;
    *) echo "$LAYA_THREADS" ;;
  esac
}

# The proxy's admission control starts from the throughput measured on the
# NAS for the device this container got (2026-11-01, Clef-Flash Q4_K_M on
# real pilot payloads: ~70 tok/s on the Iris Xe, ~15 on 12 CPU threads) and
# then learns the real rate from every answered call.
# Laya's call time is mostly fixed (0.3 s on the Iris Xe, ~2.5 s on 4 CPU
# threads for 66-263 tokens), so it is seeded as a minimum call time.
if [ -r /dev/dri/renderD128 ]; then
  : "${CLEF_EXPECTED_TOKENS_PER_S:=70}" "${LAYA_MIN_CALL_S:=0.5}"
else
  : "${CLEF_EXPECTED_TOKENS_PER_S:=15}" "${LAYA_MIN_CALL_S:=2.6}"
fi
export CLEF_EXPECTED_TOKENS_PER_S LAYA_MIN_CALL_S

start_backend() {
  local key="$1"
  local gguf
  gguf="$(gguf_of "$key")"
  if [ ! -s "$gguf" ]; then
    log "$key: GGUF missing or empty at $gguf - skipping this backend"
    return 1
  fi
  log "$key: starting llama-server with $gguf on port $(port_of "$key") (input cap $(ctx_of "$key") tokens, gpu layers $(gpu_layers_of "$key"), threads $(threads_of "$key"), alias $(alias_of "$key"))"
  "$LLAMA_SERVER_BIN" \
    --model "$gguf" \
    --host 127.0.0.1 \
    --port "$(port_of "$key")" \
    --ctx-size "$(ctx_of "$key")" \
    --batch-size "$(ctx_of "$key")" \
    --ubatch-size "$(ctx_of "$key")" \
    --parallel 1 \
    --n-gpu-layers "$(gpu_layers_of "$key")" \
    --threads "$(threads_of "$key")" \
    --alias "$(alias_of "$key")" \
    --no-webui \
    --metrics &
  LAST_PID=$!
  PIDS+=("$LAST_PID")
  log "$key: llama-server pid $LAST_PID"
  return 0
}

wait_backend() {
  local key="$1"
  local pid="$2"
  local url
  url="${CLEF_UPSTREAM_URL%/}/health"
  if [ "$key" != "clef" ]; then
    url="${LAYA_UPSTREAM_URL%/}/health"
  fi
  local waited=0
  while [ "$waited" -lt "$BACKEND_START_TIMEOUT_S" ]; do
    # A llama-server that died during startup must not cost the full
    # startup timeout before the container gets restarted.
    if ! kill -0 "$pid" 2>/dev/null; then
      log "$key: llama-server (pid $pid) exited during startup"
      return 1
    fi
    # python3 stdlib, not curl: the slim runtime image ships no curl, and a
    # 503 ("Loading model") must read as not-ready-here too.
    if python3 -c "import urllib.request; urllib.request.urlopen('$url', timeout=2)" \
      >/dev/null 2>&1; then
      log "$key: upstream healthy at $url (after ${waited}s)"
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  log "$key: upstream never became healthy at $url within ${BACKEND_START_TIMEOUT_S}s"
  return 1
}

any_backend=false
for key in clef laya; do
  case "$key" in
    clef)
      if [ "$CLEF_ENABLED" != "true" ]; then log "clef: disabled by configuration"; continue; fi
      ;;
    laya)
      if [ "$LAYA_ENABLED" != "true" ]; then log "laya: disabled by configuration"; continue; fi
      ;;
  esac
  if ! start_backend "$key"; then
    log "$key: nothing to serve; the proxy /health will report this backend down"
    continue
  fi
  if ! wait_backend "$key" "$LAST_PID"; then
    log "$key: failed to start; tearing the sidecar down for a clean restart"
    cleanup
    exit 1
  fi
  any_backend=true
done

if [ "$any_backend" = "false" ]; then
  log "no backend started: the proxy still serves /health and honest 503s"
fi

cd /app || { log "no /app to serve from"; exit 1; }
log "starting the proxy on ${DECISIONS_HOST}:${DECISIONS_PORT}"
uvicorn server:app --host "$DECISIONS_HOST" --port "$DECISIONS_PORT" &
PIDS+=("$!")

# wait -n returns as soon as ANY child dies (bash 5): a crashed
# llama-server tears the container down so restart policy recovers it,
# and a crash of the proxy itself exits the container the same way.
wait -n
status=$?
log "a child exited (status $status); stopping the rest"
cleanup
exit "$status"
