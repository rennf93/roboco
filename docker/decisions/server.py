"""roboco-decisions sidecar: the Laya tier of the Decisions service.

Serves the OpenRouter Decisions wire shape (POST /api/alpha/decisions) over
the laya library's OFFICIAL onnxruntime path (laya.onnx_agent.ONNXAgent), so
roboco/services/decisions/client.py speaks ONE implementation against the
self-hosted Laya tier and the OpenRouter fallback tier
(docs/internal/decisions-spec.md sections 8 Stage 0.5 and 10).

Dependency-light on purpose: import time needs only fastapi + stdlib. The
laya library is imported lazily inside the startup hook so this file
compiles (py_compile / ruff) on machines without laya installed.

Calibration doctrine (spec section 10): Laya checkpoints ship over-confident
until temperature fitting. The fitted temperatures live in the checkpoint's
rl_agent_config.json and are applied BY THE LIBRARY at load (clamped to
[0.5, 5.0]; per-option-count buckets in temperature_by_options take
precedence over the per-type temperature vector). This server never
recomputes or bypasses calibration; it verifies the fitted vector exists at
startup and fails every decisions request with 500 when it is missing, so
the client's circuit breaker hands the traffic to the fallback tier instead
of serving raw-logit confidences.
"""

from __future__ import annotations

import hmac
import json
import math
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

MODEL_ID = os.environ.get("LAYA_MODEL_ID", "convaiinnovations/laya")
SUBFOLDER = os.environ.get("LAYA_SUBFOLDER", "typed-decisions")
MODELS_DIR = Path(os.environ.get("LAYA_MODELS_DIR", "/models"))
LAYA_API_KEY = os.environ.get("LAYA_API_KEY", "")
HOST = os.environ.get("LAYA_HOST", "0.0.0.0")
PORT = int(os.environ.get("LAYA_PORT", "8100"))

CHECKPOINT = f"{MODEL_ID}:{SUBFOLDER}"

_VALID_TYPES = ("noul", "choice", "score")

app = FastAPI(title="roboco-decisions", version="0.1.0")

# Populated by the startup hook; read by the routes.
_state: dict[str, Any] = {
    "agent": None,
    "load_error": "",
    "calibration_error": "",
}


def _load_agent() -> Any:
    """Load the typed-decisions checkpoint via the official ONNX path.

    ONNXAgent's signature at the pinned revision:
        ONNXAgent(model_id_or_path, onnx_path="laya.onnx", subfolder=None, ...)
    Weights are baked into the image at LAYA_MODELS_DIR (snapshot pinned via
    the LAYA_HF_REVISION build arg), so try the local snapshot first and
    fall back to the hub id (which resolves to the same baked snapshot via
    the local HF cache) only if the local-path form is rejected.
    """
    from laya.onnx_agent import ONNXAgent  # noqa: PLC0415 - lazy on purpose

    local_onnx = str(MODELS_DIR / SUBFOLDER / "laya.onnx")
    candidates: list[dict[str, Any]] = [
        {
            "model_id_or_path": str(MODELS_DIR),
            "onnx_path": local_onnx,
            "subfolder": SUBFOLDER,
        },
        {
            "model_id_or_path": MODEL_ID,
            "onnx_path": local_onnx,
            "subfolder": SUBFOLDER,
        },
    ]
    last_error: Exception | None = None
    for kwargs in candidates:
        try:
            return ONNXAgent(**kwargs)
        except Exception as exc:  # fall through to the next load form
            last_error = exc
    msg = f"ONNXAgent failed to load {CHECKPOINT}: {last_error}"
    raise RuntimeError(msg) from last_error


def _calibration_error() -> str:
    """Return an error string when the checkpoint carries no fitted
    temperature vector, else "".

    rl_agent_config.json ships with the checkpoint (next to laya.onnx). The
    library's default when the "temperature" key is absent is a neutral
    [1.0, 1.0, 1.0], i.e. RAW, uncalibrated confidences - exactly what the
    spec forbids serving. So: key present, three finite positive numbers,
    one per question type (choice, score, noul). Per-option-count buckets
    (temperature_by_options) are optional by design: upstream's fitted
    typed-decisions checkpoint fits one temperature per type and strips
    stale buckets.
    """
    cfg_path = MODELS_DIR / SUBFOLDER / "rl_agent_config.json"
    try:
        with cfg_path.open(encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = getattr(_state.get("agent"), "cfg", None) or {}
    temps = cfg.get("temperature")
    if not isinstance(temps, (list, tuple)) or len(temps) != len(_VALID_TYPES):
        return (
            f"{cfg_path} carries no fitted temperature vector "
            "(choice/score/noul); refusing to serve raw-logit confidences"
        )
    if any(not isinstance(t, (int, float)) or math.isnan(t) or t <= 0 for t in temps):
        return f"{cfg_path} fitted temperature vector is not finite/positive: {temps!r}"
    return ""


@app.on_event("startup")
def _startup() -> None:
    try:
        _state["agent"] = _load_agent()
    except Exception as exc:  # keep serving so /health can report the failure
        _state["load_error"] = str(exc)
        return
    _state["calibration_error"] = _calibration_error()


def _require_ready() -> Any:
    if _state["load_error"]:
        raise HTTPException(
            status_code=503, detail=f"model not loaded: {_state['load_error']}"
        )
    if _state["calibration_error"]:
        # Fail the request OPEN (5xx): the client's circuit breaker + tier
        # resolver hand the traffic past the Laya tier instead of trusting
        # uncalibrated confidences.
        raise HTTPException(status_code=500, detail=_state["calibration_error"])
    return _state["agent"]


def _check_auth(request: Request) -> None:
    """Optional LAYA_API_KEY, timing-safe. Unused on the internal bridge
    (nothing on roboco_default carries a key) but required to exist."""
    if not LAYA_API_KEY:
        return
    provided = request.headers.get("authorization", "")
    if provided.lower().startswith("bearer "):
        provided = provided[len("bearer ") :]
    else:
        provided = request.headers.get("x-laya-key", "")
    if not provided or not hmac.compare_digest(
        provided.encode("utf-8"), LAYA_API_KEY.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid or missing Laya API key")


@app.get("/health")
def health() -> dict[str, str]:
    if _state["agent"] is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "model": CHECKPOINT,
                "error": _state["load_error"],
            },
        )
    return {"status": "healthy", "model": CHECKPOINT}


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


@app.post("/api/alpha/decisions")
async def decisions(request: Request) -> dict[str, Any]:
    _check_auth(request)
    agent = _require_ready()
    try:
        body = await request.json()
    except Exception as exc:  # malformed JSON is a 400, not a 500
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    questions = _validate_questions(body.get("questions"))
    state = body.get("state", "")

    result = agent.predict(state, questions)

    raw_answers = result.get("answers") or {}
    answers: dict[str, dict[str, Any]] = {}
    for key, spec in questions.items():
        qtype = spec["type"]
        raw = raw_answers.get(key) or {}
        out: dict[str, Any] = {"type": qtype}
        if qtype == "noul":
            out["noul"] = bool(raw.get("noul"))
        elif qtype == "choice":
            out["choice"] = raw.get("choice")
            out["confidence"] = float(raw.get("confidence") or 0.0)
            # Probabilities come from the model logits when the library
            # surfaces them on the answer; confidence is always present.
            probs = raw.get("probabilities") or raw.get("probs")
            if probs:
                out["probabilities"] = probs
        else:  # score
            out["score"] = raw.get("score")
            out["confidence"] = float(raw.get("confidence") or 0.0)
            legend = raw.get("legend")
            if legend:
                out["legend"] = legend
        answers[key] = out

    usage = result.get("usage") or {}
    # session_id is accepted but unused: the sidecar is stateless and the
    # client owns session bookkeeping (spec section 4).
    return {
        "id": f"dec-{uuid.uuid4().hex}",
        "model": body.get("model") or CHECKPOINT,
        "answers": answers,
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cost": 0.0,  # self-hosted tier: $0 by construction
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
