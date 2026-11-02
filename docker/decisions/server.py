"""roboco-decisions sidecar: the self-hosted tier of the Decisions service.

A dependency-light FastAPI PROXY in front of two llama.cpp decision
servers (llama.cpp PR #29818, first tag carrying the endpoint: b11361),
each started by docker/decisions/entrypoint.sh in this container:

  - clef tier (default): ggml-org/Clef-Flash-GGUF, a 9B decision model
    (Apache-2.0), llama-server on 127.0.0.1:8110;
  - laya (cheap option): ggml-org/Laya-GGUF, a 421M ModernBERT decision
    model (Apache-2.0), llama-server on 127.0.0.1:8111.

Serves the OpenRouter Decisions wire shape (POST /api/alpha/decisions) so
roboco/services/decisions/client.py speaks ONE implementation against the
self-hosted tiers and the OpenRouter fallback tier
(docs/internal/decisions-spec.md sections 8 Stage 0.5 and 10). The proxy
forwards {state, questions} to the routed upstream's /v1/systemone
verbatim and maps the answer into the exact envelope this sidecar has
always returned; usage.cost stays 0.0 (self-hosted is $0 by construction).

Calibration doctrine: llama.cpp decision servers scale probabilities with
the model-native temperatures stored in the GGUF metadata (server README:
"The probabilities are scaled with the temperatures stored in the model
file"), so the old ONNX-era rl_agent_config.json gate is deleted. What
replaces it is the honest-health gate: /health 503s while the clef
upstream is unreachable, so the orchestrator's health probe and the
client's circuit breaker hand traffic to the fallback tier instead of
black-holing into a dead sidecar. The answer normalization below (bool
noul coercion, gating-confidence recovery from the reported answer's
probability) is load-bearing regression surface: the orchestrator's
parser rejects booleans on purpose, and an entropy-style summary
confidence would permanently fail every 0.6-0.8 gating floor.

Admission control: each llama-server serves ONE input at a time and keeps
no KV cache, so an input cannot be paused or cancelled once it runs. The
proxy queues per backend and admits an input only when its estimated
finish (the queue ahead of it plus its own estimated service time) falls
inside the caller's deadline (``x-decisions-deadline-s``); otherwise it
answers 503 ``busy:`` at once, which the orchestrator's client reads as no
verdict WITHOUT counting a backend failure. Estimates start from the
throughput measured on the NAS and are learned from every answered call.

The proxy needs no llama.cpp at import time: tests import this file
directly and stub the upstream with an httpx.MockTransport.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# ---------------------------------------------------------------------------
# Configuration (env)
# ---------------------------------------------------------------------------

DECISIONS_API_KEY = os.environ.get(
    "DECISIONS_API_KEY", os.environ.get("LAYA_API_KEY", "")
)
# Legacy LAYA_* fallbacks ride one release after the env rename.
HOST = os.environ.get("DECISIONS_HOST", os.environ.get("LAYA_HOST", "0.0.0.0"))
PORT = int(os.environ.get("DECISIONS_PORT", os.environ.get("LAYA_PORT", "8100")))
UPSTREAM_TIMEOUT_S = float(os.environ.get("UPSTREAM_TIMEOUT_S", "180.0"))
HEALTH_TIMEOUT_S = 2.0


def _enabled(name: str) -> bool:
    return os.environ.get(name, "true").strip().lower() != "false"


@dataclass(frozen=True)
class _Backend:
    """One llama.cpp decision server this proxy fronts."""

    key: str
    model_id: str
    upstream_url: str
    state_char_cap: int
    enabled: bool


CLEF = _Backend(
    key="clef",
    model_id=os.environ.get("CLEF_MODEL_ID", "ggml-org/Clef-Flash-GGUF:Q4_K_M"),
    upstream_url=os.environ.get("CLEF_UPSTREAM_URL", "http://127.0.0.1:8110").rstrip(
        "/"
    ),
    # Two chars per token of the input cap: the orchestrator's client trims
    # every clef state to the same budget (decisions/client.py), and
    # real pilot payloads ran 2.3 chars per token (median of 45, NAS
    # 2026-11-01), so a state under this cap fits the backend's batch.
    state_char_cap=2 * int(os.environ.get("CLEF_MAX_INPUT_TOKENS", "8192")),
    enabled=_enabled("CLEF_ENABLED"),
)
LAYA = _Backend(
    key="laya",
    model_id=os.environ.get("LAYA_MODEL_ID", "ggml-org/Laya-GGUF"),
    upstream_url=os.environ.get("LAYA_UPSTREAM_URL", "http://127.0.0.1:8111").rstrip(
        "/"
    ),
    state_char_cap=1_800,
    enabled=_enabled("LAYA_ENABLED"),
)

_VALID_TYPES = ("noul", "choice", "score")

# Shared upstream client, created lazily so tests can swap it for a
# MockTransport-backed client before the first request. A holder dict (the
# old server's _state pattern) instead of a module global: rebinding a
# module attribute from the outside is what PLW0603 exists to flag.
_HTTP: dict[str, httpx.AsyncClient | None] = {"client": None}


def _http_client() -> httpx.AsyncClient:
    client = _HTTP["client"]
    if client is None:
        client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_S)
        _HTTP["client"] = client
    return client


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Nothing to load at startup (the llama-servers own the models); just
    close the shared upstream client on shutdown."""
    yield
    client = _HTTP["client"]
    if client is not None:
        await client.aclose()


app = FastAPI(title="roboco-decisions", version="0.2.0", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _route(model: Any) -> _Backend:
    """Map the request's ``model`` field onto a backend.

    Contains "clef" (case-insensitive) -> clef; contains "laya" -> laya;
    absent or anything else -> clef, the default sweet spot tier. The
    orchestrator's ``decisions_model`` setting names whatever checkpoint
    id it wants on the wire; the sidecar only ever reads the tier hint
    out of it, never downloads or resolves it.
    """
    name = str(model or "").lower()
    if "clef" in name:
        return CLEF
    if "laya" in name:
        return LAYA
    return CLEF


# ---------------------------------------------------------------------------
# Admission control
# ---------------------------------------------------------------------------

DEADLINE_HEADER = "x-decisions-deadline-s"
# Median chars per token over 45 real pilot payloads (state + questions JSON
# against llama.cpp's reported input_tokens), NAS 2026-11-01.
_CHARS_PER_TOKEN_START = 2.3
# How far one answered call moves a learned estimate.
_LEARNING_WEIGHT = 0.2


def _blend(old: float, new: float) -> float:
    return old + _LEARNING_WEIGHT * (new - old)


@dataclass
class ServiceTime:
    """How long one input takes on a backend: a per-token time learned from
    answered calls, never below the backend's minimum call time."""

    seconds_per_token: float
    min_call_s: float
    chars_per_token: float = _CHARS_PER_TOKEN_START

    def estimate_s(self, chars: int) -> float:
        tokens = chars / self.chars_per_token
        return max(self.min_call_s, tokens * self.seconds_per_token)

    def learn(self, chars: int, tokens: int, seconds: float) -> None:
        if tokens <= 0 or seconds <= 0:
            return
        self.chars_per_token = _blend(self.chars_per_token, chars / tokens)
        # A call near the minimum call time says nothing about the rate.
        if seconds > 2 * self.min_call_s:
            self.seconds_per_token = _blend(self.seconds_per_token, seconds / tokens)


class BackendBusy(Exception):
    """The input cannot finish before its caller's deadline."""

    def __init__(self, wait_s: float, estimate_s: float, deadline_s: float) -> None:
        super().__init__(
            f"busy: about {wait_s:.0f}s of queued work plus about "
            f"{estimate_s:.0f}s for this input exceeds the {deadline_s:.0f}s deadline"
        )


class AdmissionQueue:
    """One input at a time on a backend, admitted only when it can finish
    before its caller's deadline."""

    def __init__(self, service_time: ServiceTime) -> None:
        self.service_time = service_time
        self._lock = asyncio.Lock()
        self._waiting_s = 0.0
        self._running_until = 0.0

    def expected_wait_s(self) -> float:
        running_left = max(self._running_until - time.monotonic(), 0.0)
        return running_left + self._waiting_s

    @asynccontextmanager
    async def slot(self, estimate_s: float, deadline_s: float) -> AsyncIterator[float]:
        """Hold the backend for one input; yields the seconds left before the
        deadline. Raises BackendBusy instead of starting a late input."""
        started = time.monotonic()
        wait_s = self.expected_wait_s()
        if wait_s + estimate_s > deadline_s:
            raise BackendBusy(wait_s, estimate_s, deadline_s)
        self._waiting_s += estimate_s
        waiting = True
        try:
            async with self._lock:
                self._waiting_s -= estimate_s
                waiting = False
                left_s = deadline_s - (time.monotonic() - started)
                if left_s < estimate_s:
                    raise BackendBusy(deadline_s - left_s, estimate_s, deadline_s)
                self._running_until = time.monotonic() + estimate_s
                try:
                    yield left_s
                finally:
                    self._running_until = 0.0
        finally:
            if waiting:
                self._waiting_s -= estimate_s


def _env_float(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


# Seeded from the device the entrypoint found (CLEF_EXPECTED_TOKENS_PER_S:
# ~70 tok/s on the Iris Xe, ~15 on the CPU; LAYA_MIN_CALL_S: 0.5 s GPU,
# 2.6 s CPU), measured on the NAS 2026-11-01.
_QUEUES: dict[str, AdmissionQueue] = {}


def admission_queue(backend: _Backend) -> AdmissionQueue:
    """The backend's queue, created on first use."""
    queue = _QUEUES.get(backend.key)
    if queue is None:
        if backend.key == "clef":
            service = ServiceTime(
                seconds_per_token=1 / _env_float("CLEF_EXPECTED_TOKENS_PER_S", "15"),
                min_call_s=_env_float("CLEF_MIN_CALL_S", "2.0"),
            )
        else:
            service = ServiceTime(
                seconds_per_token=1 / _env_float("LAYA_EXPECTED_TOKENS_PER_S", "100"),
                min_call_s=_env_float("LAYA_MIN_CALL_S", "2.6"),
            )
        queue = AdmissionQueue(service)
        _QUEUES[backend.key] = queue
    return queue


def _deadline_s(request: Request) -> float:
    """The caller's deadline, clamped to [1 s, UPSTREAM_TIMEOUT_S]."""
    raw = request.headers.get(DEADLINE_HEADER, "")
    try:
        deadline = float(raw) if raw else UPSTREAM_TIMEOUT_S
    except ValueError:
        deadline = UPSTREAM_TIMEOUT_S
    return min(max(deadline, 1.0), UPSTREAM_TIMEOUT_S)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _check_auth(request: Request) -> None:
    """Optional DECISIONS_API_KEY, timing-safe. Unused on the internal
    bridge (nothing on roboco_default carries a key) but required to
    exist. ``x-decisions-key`` is the canonical header; the legacy
    ``x-laya-key`` and ``Authorization: Bearer`` forms stay accepted for
    one release after the env rename."""
    if not DECISIONS_API_KEY:
        return
    provided = request.headers.get("x-decisions-key", "")
    if not provided:
        provided = request.headers.get("x-laya-key", "")
    if not provided:
        provided = request.headers.get("authorization", "")
        if provided.lower().startswith("bearer "):
            provided = provided[len("bearer ") :]
        else:
            provided = ""
    if not provided or not hmac.compare_digest(
        provided.encode("utf-8"), DECISIONS_API_KEY.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401, detail="invalid or missing decisions API key"
        )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def _probe(backend: _Backend) -> dict[str, Any]:
    """Probe one upstream's llama.cpp /health (public, no key).

    ok=false covers both a deliberate disable and an unreachable/starting
    upstream; the detail string says which. llama.cpp answers 503 with
    "Loading model" while the GGUF is still on disk -> not ready.
    """
    if not backend.enabled:
        return {
            "ok": False,
            "enabled": False,
            "detail": (
                f"disabled by configuration ({backend.key.upper()}_ENABLED=false)"
            ),
        }
    try:
        response = await _http_client().get(
            f"{backend.upstream_url}/health", timeout=HEALTH_TIMEOUT_S
        )
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "enabled": True,
            "detail": f"upstream unreachable: {exc}",
        }
    if response.status_code != httpx.codes.OK:
        detail = f"upstream not ready: HTTP {response.status_code}"
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = ""
        if message:
            detail = f"{detail} ({message})"
        return {"ok": False, "enabled": True, "detail": detail}
    queue = admission_queue(backend)
    return {
        "ok": True,
        "enabled": True,
        "detail": backend.model_id,
        "queue": {
            "expected_wait_s": round(queue.expected_wait_s(), 1),
            "seconds_per_token": round(queue.service_time.seconds_per_token, 4),
            "chars_per_token": round(queue.service_time.chars_per_token, 2),
        },
    }


@app.get("/health")
async def health() -> JSONResponse:
    """Per-backend health with an honest aggregate.

    200 while the clef tier can serve (clef healthy, whatever laya does -
    the cheap tier being down must not take the primary out of the
    orchestrator's health view), 503 only when an ENABLED clef upstream
    is unreachable or every backend is disabled: the resolver's cached
    probe checks the status code alone (30s TTL), so a dead primary must
    surface in the status line or the client circuit breaker cycles
    forever. "ok" means both backends actually serve; a disabled backend
    (deliberate configuration, not a fault) reports ok=false with a
    "disabled" detail under a 200-degraded aggregate.
    """
    clef_status = await _probe(CLEF)
    laya_status = await _probe(LAYA)
    primary_down = CLEF.enabled and not clef_status["ok"]
    nothing_enabled = not CLEF.enabled and not LAYA.enabled
    body: dict[str, Any] = {
        "status": ("ok" if (clef_status["ok"] and laya_status["ok"]) else "degraded"),
        "clef": clef_status,
        "laya": laya_status,
    }
    if primary_down or nothing_enabled:
        return JSONResponse(status_code=503, content=body)
    return JSONResponse(status_code=200, content=body)


# ---------------------------------------------------------------------------
# Answer normalization (the regression-tested seam)
# ---------------------------------------------------------------------------


def _validate_questions(questions: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(questions, dict) or not questions:
        raise HTTPException(
            status_code=400, detail="questions must be a non-empty object"
        )
    for key, spec in questions.items():
        if not isinstance(spec, dict):
            raise HTTPException(
                status_code=400, detail=f"questions[{key}] must be an object"
            )
        if spec.get("type") not in _VALID_TYPES:
            allowed = ", ".join(_VALID_TYPES)
            raise HTTPException(
                status_code=400,
                detail=f"questions[{key}].type must be one of {allowed}",
            )
        if not isinstance(spec.get("instructions", ""), str):
            raise HTTPException(
                status_code=400,
                detail=f"questions[{key}].instructions must be a string",
            )
    return questions


def _probability(value: Any) -> float | None:
    """Coerce one raw model output into a ``[0.0, 1.0]`` probability.

    Upstream answer shapes have varied across runtimes (float probability,
    0/1 bool, numeric string, ``{"probability": ...}`` dict); the wire
    format needs a float either way. The orchestrator's parser rejects
    booleans ON PURPOSE (a bool is not a calibrated confidence), so
    coercing here - never emitting a bare bool - is what keeps the noul
    surface alive. NaN and unparseable shapes return None and the answer
    is omitted entirely, which the client reads as "no verdict for this
    question" (the fail-open direction, and the fail-CLOSED direction for
    the injection screens)."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        prob: float | None = float(value)
    elif isinstance(value, str):
        try:
            prob = float(value)
        except ValueError:
            prob = None
    elif isinstance(value, dict):
        for key in ("probability", "prob", "noul", "confidence", "score"):
            coerced = _probability(value.get(key))
            if coerced is not None:
                return coerced
        prob = None
    else:
        prob = None
    if prob is None or math.isnan(prob):
        return None
    return max(0.0, min(1.0, prob))


def _gating_confidence(raw: dict[str, Any]) -> float | None:
    """The confidence the wire contract means: the probability of the
    REPORTED answer.

    llama.cpp's choice/score ``confidence`` is the TypeSafe-published
    rescaled-distance formula, not the answer probability (verified
    against tools/server/server-decision.cpp at the pinned tag: for a
    0.9/0.05/0.05 split it yields ~0.85 while the max bucket is 0.9; for
    flatter splits the gap widens). Gating every pilot's 0.6-0.8 floors
    on a summary that reads below them would fail decisive verdicts, so
    the answer probability is recovered in priority order:
    ``answer_confidence`` when the shape carries it, else the max bucket
    probability, else the upstream summary as a last resort."""
    probs = [p for p in (_probability(v) for v in _probs_values(raw)) if p is not None]
    candidates = [_probability(raw.get("answer_confidence"))]
    if probs:
        candidates.append(max(probs))
    candidates.append(_probability(raw.get("confidence")))
    for source in candidates:
        if source is not None:
            return source
    return None


def _probs_values(raw: dict[str, Any]) -> list[Any]:
    for key in ("probabilities", "probs"):
        probs = raw.get(key)
        if isinstance(probs, dict) and probs:
            return list(probs.values())
    return []


def build_answers(questions: dict[str, dict[str, Any]], raw_answers: Any) -> dict:
    """Build the wire answers for one predicted batch, leniently.

    A question whose raw answer carries no usable payload is OMITTED
    rather than emitted with fabricated zeros: the client's lenient
    parser treats a missing answer as "no verdict", and the
    fail-closed consumers flag on exactly that. This function is the
    single seam the contract test exercises (the orchestrator parses
    its output under the same schemas.py as OpenRouter's)."""
    if not isinstance(raw_answers, dict):
        raw_answers = {}
    answers: dict[str, dict[str, Any]] = {}
    for key, spec in questions.items():
        qtype = spec["type"]
        raw = raw_answers.get(key) or {}
        if not isinstance(raw, dict):
            raw = {}
        out: dict[str, Any] = {"type": qtype}
        if qtype == "noul":
            noul = _probability(raw.get("noul"))
            if noul is None:
                continue
            out["noul"] = noul
            # llama.cpp's noul answer is {type, noul} only: the probability
            # IS the answer probability, so it doubles as the confidence.
            out["confidence"] = _probability(raw.get("confidence")) or noul
        elif qtype == "choice":
            choice = raw.get("choice")
            confidence = _gating_confidence(raw)
            if choice is None or confidence is None:
                continue
            out["choice"] = str(choice)
            out["confidence"] = confidence
            # Probabilities come from the upstream logits; llama.cpp always
            # carries the option-keyed distribution on choice answers.
            probs = raw.get("probabilities") or raw.get("probs")
            if isinstance(probs, dict) and probs:
                out["probabilities"] = probs
        else:  # score
            score = raw.get("score")
            confidence = _gating_confidence(raw)
            if score is None or confidence is None:
                continue
            out["score"] = score
            out["confidence"] = confidence
            legend = raw.get("legend")
            if legend:
                out["legend"] = legend
        answers[key] = out
    return answers


# ---------------------------------------------------------------------------
# Wire routes
# ---------------------------------------------------------------------------


def _state_char_len(state: Any) -> int:
    """The char budget llama.cpp actually sees: a string state passes
    through verbatim, anything else goes to the model as JSON text
    (server README, /v1/systemone)."""
    if state is None:
        return 0
    if isinstance(state, str):
        return len(state)
    return len(json.dumps(state, ensure_ascii=False, default=str))


def _upstream_error(backend: _Backend, response: httpx.Response, prefix: str) -> str:
    detail = response.text[:500]
    try:
        message = response.json().get("error", {}).get("message", "")
    except ValueError:
        message = ""
    if message:
        detail = message
    return f"{backend.key} upstream {prefix} HTTP {response.status_code}: {detail}"


@app.post("/api/alpha/decisions")
async def decisions(request: Request) -> dict[str, Any]:
    _check_auth(request)
    try:
        body = await request.json()
    except Exception as exc:  # malformed JSON is a 400, not a 500
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    questions = _validate_questions(body.get("questions"))
    state = body.get("state", "")
    backend = _route(body.get("model"))
    _refuse_disabled_or_oversize(backend, state)

    chars = _state_char_len(state) + len(json.dumps(questions, ensure_ascii=False))
    queue = admission_queue(backend)
    try:
        async with queue.slot(
            queue.service_time.estimate_s(chars), _deadline_s(request)
        ) as left_s:
            started = time.monotonic()
            payload = await _ask_upstream(backend, state, questions, left_s)
            elapsed = time.monotonic() - started
    except BackendBusy as busy:
        raise HTTPException(status_code=503, detail=str(busy)) from busy

    answers = build_answers(questions, (payload or {}).get("answers"))
    usage = (payload or {}).get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    queue.service_time.learn(chars, input_tokens, elapsed)
    # session_id is accepted but unused: the sidecar is stateless and the
    # client owns session bookkeeping (spec section 4).
    return {
        "id": f"dec-{uuid.uuid4().hex}",
        "model": body.get("model") or backend.model_id,
        "answers": answers,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cost": 0.0,  # self-hosted tier: $0 by construction
        },
    }


def _refuse_disabled_or_oversize(backend: _Backend, state: Any) -> None:
    if not backend.enabled:
        # No silent rerouting: the caller asked for a tier (cheap screening
        # vs wide context) and the orchestrator's resolver owns fallback.
        raise HTTPException(
            status_code=503,
            detail=(
                f"{backend.key} tier is disabled on this sidecar "
                f"({backend.key.upper()}_ENABLED=false); route to another tier"
            ),
        )
    state_len = _state_char_len(state)
    if state_len > backend.state_char_cap:
        # Defensive second gate behind the orchestrator's own budgets: a
        # direct caller skips the client's cap passes, so the sidecar
        # refuses oversize states itself instead of feeding a context
        # overflow into the upstream.
        raise HTTPException(
            status_code=422,
            detail=(
                f"state exceeds the {backend.key} tier budget: {state_len} chars "
                f"> {backend.state_char_cap}; trim the state or route to a wider tier"
            ),
        )


async def _ask_upstream(
    backend: _Backend, state: Any, questions: dict[str, Any], timeout_s: float
) -> Any:
    """One /v1/systemone call; HTTP errors mapped onto the wire contract."""
    try:
        response = await _http_client().post(
            f"{backend.upstream_url}/v1/systemone",
            json={"state": state, "questions": questions},
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{backend.key} upstream unreachable: {exc}",
        ) from exc
    if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
        raise HTTPException(
            status_code=502,
            detail=_upstream_error(backend, response, "error"),
        )
    if response.status_code >= httpx.codes.BAD_REQUEST:
        raise HTTPException(
            status_code=response.status_code,
            detail=_upstream_error(backend, response, "rejected the request with"),
        )
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{backend.key} upstream returned a non-JSON body",
        ) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
