"""LIVE contract tests against a running roboco-decisions sidecar.

These are the checks unit tests cannot give: the REAL laya library, the
REAL typed-decisions checkpoint, real ONNX inference. Skipped unless
``ROBOCO_SIDECAR_LIVE_URL`` is set - the decisions-sidecar GitHub workflow
starts the sidecar and sets it; local runs opt in the same way::

    ROBOCO_SIDECAR_LIVE_URL=http://127.0.0.1:8100 \
        uv run pytest tests/integration/test_decisions_sidecar_live.py -v

Pinned regressions this file exists to catch (both shipped silently once):
a noul answer that is not a float probability, and a /health that passes
while the calibration gate is actually failing.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

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


@pytest.fixture
def endpoint() -> DecisionsEndpoint:
    return DecisionsEndpoint(
        tier="laya",
        base_url=LIVE_URL,
        model="convaiinnovations/laya",
        timeout_s=_TIMEOUT,
    )


@pytest.fixture
async def client() -> AsyncIterator[DecisionsClient]:
    c = DecisionsClient(http_client=httpx.AsyncClient(timeout=_TIMEOUT))
    yield c
    await c.aclose()


async def test_health_is_200_and_reports_the_checkpoint() -> None:
    """200 means the model loaded AND the fitted-temperature calibration
    gate passed (the sidecar 503s /health on a raw-temperature checkpoint
    rather than serve uncalibrated confidences)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(f"{LIVE_URL}/health")
    assert response.status_code == httpx.codes.OK, response.text
    body = response.json()
    assert body["status"] == "healthy"
    assert "typed-decisions" in body["model"]


@pytest.mark.asyncio
async def test_live_noul_is_a_float_probability_with_confidence(
    client: DecisionsClient, endpoint: DecisionsEndpoint
) -> None:
    """The regression that once dead-ended the whole default tier: the
    adapter must deliver a noul as a float probability WITH a confidence,
    never a bool."""
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
    under the client timeout even on a shared CI runner (published p50 is
    tens of ms; this bound is 200x that)."""
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
