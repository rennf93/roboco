"""LIVE contract tests against a running roboco-decisions sidecar.

These are the checks unit tests cannot give: a REAL llama.cpp decision
server (llama-server at the pinned tag) serving the REAL Laya-GGUF
through the REAL FastAPI proxy. Skipped unless ``ROBOCO_SIDECAR_LIVE_URL``
is set - the decisions-sidecar GitHub workflow starts the stack and sets
it; local runs opt in the same way::

    ROBOCO_SIDECAR_LIVE_URL=http://127.0.0.1:8100 \
        uv run pytest tests/integration/test_decisions_sidecar_live.py -v

Every decisions test routes to the LAYA tier (the model hint contains
"laya"), which is what CI ships (CLEF_ENABLED=false, no 19GB clef GGUF on
a shared runner). The health test tolerates both postures: a full local
stack reports "ok" with clef serving, the CI stack reports "degraded"
with clef disabled.

Pinned regressions this file exists to catch (one shipped silently): a
noul answer that is not a float probability with a confidence.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from roboco.services.decisions.client import (
    DecisionsClient,
    DecisionsEndpoint,
)
from roboco.services.decisions.schemas import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LIVE_URL = os.environ.get("ROBOCO_SIDECAR_LIVE_URL", "")
pytestmark = pytest.mark.skipif(
    not LIVE_URL,
    reason="live sidecar not requested: set ROBOCO_SIDECAR_LIVE_URL",
)

_TIMEOUT = 30.0
_SCORE_LEGEND_MAX = 2
_SINGLE_QUESTION_LATENCY_BOUND_S = 5.0

# The CI/live stack routes by model hint: any id containing "laya" lands
# on the llama.cpp laya upstream regardless of the checkpoint id the
# orchestrator's resolver would name.
_LAYA_MODEL = "ggml-org/Laya-GGUF"
_CLEF_MODEL = "ggml-org/Clef-GGUF:Q4_K_M"


@pytest.fixture
def endpoint() -> DecisionsEndpoint:
    return DecisionsEndpoint(
        tier="laya",
        base_url=LIVE_URL,
        model=_LAYA_MODEL,
        timeout_s=_TIMEOUT,
    )


@pytest.fixture
async def client() -> AsyncIterator[DecisionsClient]:
    c = DecisionsClient(http_client=httpx.AsyncClient(timeout=_TIMEOUT))
    yield c
    await c.aclose()


async def _health() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(f"{LIVE_URL}/health")
    assert response.status_code in (httpx.codes.OK, httpx.codes.SERVICE_UNAVAILABLE), (
        response.text
    )
    body: dict[str, Any] = response.json()
    return body


async def test_health_reports_per_backend_status() -> None:
    """The aggregate must be coherent with the per-backend truth: "ok"
    means every enabled backend serves; "degraded" under a 200 means the
    primary (clef) serves and the cheap tier does not; clef down surfaces
    as a 503 (the honest-health gate) with laya still ok beside it."""
    body = await _health()
    assert body["status"] in ("ok", "degraded"), body
    assert body["laya"]["ok"] is True, body
    if body["status"] == "ok":
        assert body["clef"]["ok"] is True, body
    elif "disabled" in str(body["clef"]["detail"]):
        # CI posture: clef disabled by configuration, laya still serving.
        pass
    else:
        pytest.fail(f"degraded health without a disabled-clef explanation: {body}")


@pytest.mark.asyncio
async def test_live_noul_is_a_float_probability_with_confidence(
    client: DecisionsClient, endpoint: DecisionsEndpoint
) -> None:
    """The regression that once dead-ended the whole default tier: the
    adapter must deliver a noul as a float probability WITH a confidence,
    never a bool (llama.cpp's noul answer carries no confidence field at
    all - the proxy doubles the probability as the gating confidence)."""
    result = await client.decide(
        endpoint,
        {
            "repo": "roboco",
            "error_excerpt": (
                "ConnectionError: HTTPSConnectionPool(host='example.com', "
                "port=443): Read timed out. (read timeout=10)"
            ),
            "attempt_number": 1,
        },
        {
            "gate": NoulQuestion(
                instructions=(
                    "This failure is transient (infra flake) rather than a "
                    "defect in the recent commits."
                )
            )
        },
        "live:noul",
    )
    assert result is not None
    assert result.tier == "laya"
    assert result.usage.cost == 0.0
    answer = result.answer("gate")
    assert answer is not None, f"noul answer missing in raw: {result.raw}"
    assert isinstance(answer.noul, float)
    assert not isinstance(answer.noul, bool)
    assert 0.0 <= answer.noul <= 1.0
    assert answer.confidence is not None


@pytest.mark.asyncio
async def test_live_choice_carries_confidence(
    client: DecisionsClient, endpoint: DecisionsEndpoint
) -> None:
    result = await client.decide(
        endpoint,
        {
            "agent_slug": "be-dev-1",
            "upstream_status": 429,
            "attempts": 2,
        },
        {
            "gate": ChoiceQuestion(
                instructions=("Which handling applies to this rate-limit park?"),
                criteria={
                    "park_standard": "wait out the usual retry-after window",
                    "retry_soon": "a short retry is likely to succeed",
                    "escalate": "repeated failures worth a PM notification",
                },
            )
        },
        "live:choice",
    )
    assert result is not None
    answer = result.answer("gate")
    assert answer is not None, f"choice answer missing in raw: {result.raw}"
    assert answer.choice in {"park_standard", "retry_soon", "escalate"}
    assert answer.confidence is not None
    assert 0.0 <= answer.confidence <= 1.0


@pytest.mark.asyncio
async def test_live_score_is_within_the_legend(
    client: DecisionsClient, endpoint: DecisionsEndpoint
) -> None:
    result = await client.decide(
        endpoint,
        {
            "task_title": "Bump redis client to 5.x",
            "task_description": "Routine lockfile-only dependency bump.",
        },
        {
            "gate": ScoreQuestion(
                instructions="How heavy is this task?",
                criteria=[
                    "Light, mechanical: small, well-scoped",
                    "Standard delivery: normal feature scope",
                    "Heavy, multi-system: wide blast radius",
                ],
            )
        },
        "live:score",
    )
    assert result is not None
    answer = result.answer("gate")
    assert answer is not None, f"score answer missing in raw: {result.raw}"
    assert answer.score is not None
    assert 0 <= answer.score <= _SCORE_LEGEND_MAX
    assert answer.confidence is not None


@pytest.mark.asyncio
async def test_live_batched_ask_answers_every_question(
    client: DecisionsClient, endpoint: DecisionsEndpoint
) -> None:
    """Batching is nearly latency-free per the wire contract: one state, N
    typed answers back."""
    result = await client.decide(
        endpoint,
        {"task_title": "Add healthcheck endpoint", "diff": "+def health(): ..."},
        {
            "addresses": NoulQuestion(
                instructions="The diff plausibly implements a health endpoint."
            ),
            "hygiene": NoulQuestion(
                instructions="The diff contains debug leftovers or conflict markers."
            ),
            "weight": ScoreQuestion(
                instructions="How heavy is this change?",
                criteria=["Trivial", "Moderate", "Substantial"],
            ),
        },
        "live:batch",
    )
    assert result is not None
    for key in ("addresses", "hygiene", "weight"):
        assert result.answer(key) is not None, (
            f"batched answer {key} missing in raw: {result.raw}"
        )


@pytest.mark.asyncio
async def test_live_latency_is_bounded(
    client: DecisionsClient, endpoint: DecisionsEndpoint
) -> None:
    """A single-typed-question read on the tiny checkpoint must stay far
    under the client timeout even on a shared CI runner (a 421M encoder
    read is tens of ms; this bound is ~100x that)."""
    start = time.monotonic()
    result = await client.decide(
        endpoint,
        {"text": "ping"},
        {"gate": NoulQuestion(instructions="This text is a greeting.")},
        "live:latency",
    )
    elapsed = time.monotonic() - start
    assert result is not None
    assert elapsed < _SINGLE_QUESTION_LATENCY_BOUND_S, (
        f"single-question read took {elapsed:.2f}s"
    )


@pytest.mark.asyncio
async def test_live_clef_routed_request_503s_while_clef_is_absent(
    client: DecisionsClient,
) -> None:
    """The CI posture (CLEF_ENABLED=false) must degrade cleanly: a
    clef-routed request refuses with 503 and a remediation detail instead
    of silently rerouting to the cheap tier. Skips on a full local stack
    where clef is actually serving."""
    body = await _health()
    if body["clef"]["ok"]:
        pytest.skip("clef is serving on this stack; nothing to refuse")
    clef_endpoint = DecisionsEndpoint(
        tier="clef",
        base_url=LIVE_URL,
        model=_CLEF_MODEL,
        timeout_s=_TIMEOUT,
    )
    result = await client.decide(clef_endpoint, {"repo": "roboco"}, {}, "live:clef")
    # The client maps every refusal to None (fail-open at the tier level);
    # the raw HTTP surface is pinned by the workflow's explicit 503 check.
    assert result is None
