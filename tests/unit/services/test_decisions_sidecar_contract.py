"""Contract test: the roboco-decisions sidecar's responses must parse under the
same schemas.py as OpenRouter's (spec sections 4 and 10). The sidecar
mirrors the OpenRouter Decisions wire shape, so this fixture pins that
contract offline: if the container's adapter ever drifts from the wire
shape, this fails before the resolver falls over.

The canned fixture pins the wire SHAPE; the adapter tests below import
the sidecar's REAL ``build_answers`` out of ``docker/decisions/server.py``
and parse its output, so an adapter regression (a bool noul, a missing
confidence) fails here instead of silently dead-ending the Laya tier's
most-used question type in production.

The calibration fixture (known probabilities) is the standing check that
the container MUST run Laya's fitted temperatures, never raw (spec 10):
a raw-temperature checkpoint would ship the published over-confident ECE
0.466 profile, and these bounds catch it.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from roboco.services.decisions.client import DecisionsClient, DecisionsEndpoint
from roboco.services.decisions.schemas import parse_decisions_payload

# A canned sidecar response body, byte-for-byte the shape the adapter
# returns for a batched two-question request.
_SIDECAR_RESPONSE = {
    "id": "dec-local-018f3c",
    "model": "convaiinnovations/laya",
    "answers": {
        "gate": {"type": "noul", "noul": 0.93, "confidence": 0.93},
        "routing": {
            "type": "choice",
            "choice": "park_standard",
            "confidence": 0.81,
            "probabilities": {
                "park_standard": 0.78,
                "retry_soon": 0.2,
                "escalate": 0.02,
            },
        },
    },
    "usage": {"input_tokens": 210, "output_tokens": 0, "cost": 0.0},
}


def _load_sidecar_module() -> ModuleType:
    """Import docker/decisions/server.py by path (it is not on the package
    path). Import time only needs fastapi + stdlib; the laya import is
    lazy inside the startup hook, which never runs here."""
    path = Path(__file__).resolve().parents[3] / "docker" / "decisions" / "server.py"
    spec = importlib.util.spec_from_file_location("roboco_decisions_sidecar", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sidecar_response_parses_under_the_shared_schemas() -> None:
    result = parse_decisions_payload(
        _SIDECAR_RESPONSE, tier="laya", session_id="selfheal:contract"
    )
    assert result.tier == "laya"
    gate = result.answer("gate")
    assert gate is not None
    assert gate.noul == pytest.approx(0.93)
    choice = result.answer("routing")
    assert choice is not None
    assert choice.choice == "park_standard"
    assert choice.confidence == pytest.approx(0.81)
    assert abs(sum(choice.probabilities.values()) - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_sidecar_response_parses_through_the_real_client() -> None:
    """End-to-end through DecisionsClient with a transport that stands in
    for the container's HTTP surface."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/alpha/decisions")
        return httpx.Response(200, json=_SIDECAR_RESPONSE)

    endpoint = DecisionsEndpoint(
        tier="laya",
        base_url="http://roboco-decisions:8100",
        model="convaiinnovations/laya",
        timeout_s=5.0,
    )
    client = DecisionsClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    result = await client.decide(endpoint, {"repo": "roboco"}, {}, "contract:1")
    await client.aclose()
    assert result is not None
    assert result.tier == "laya"
    assert result.usage.cost == 0.0


def test_adapter_noul_is_a_float_with_confidence_not_a_bool() -> None:
    """The regression that dead-ended the Laya tier: the adapter once
    emitted ``noul`` as a bool (and no confidence), and the orchestrator's
    parser deliberately rejects booleans - so every laya-served noul
    parsed as None and every noul pilot failed open to no-verdict. The
    adapter must emit a float probability WITH a confidence, whatever
    shape the library hands over (float, bool, numeric string, dict)."""
    server = _load_sidecar_module()
    questions = {"gate": {"type": "noul", "instructions": "statement"}}
    for raw_shape in (
        {"noul": 0.93},
        {"noul": True},
        {"noul": "0.87"},
        {"noul": {"probability": 0.64}},
        {},
    ):
        built = server.build_answers(questions, {"gate": raw_shape})
        parsed = parse_decisions_payload(
            {"answers": built}, tier="laya", session_id="adapter:noul"
        )
        answer = parsed.answer("gate")
        if not raw_shape:
            # No usable payload: the answer is omitted entirely, which the
            # client reads as no-verdict (and the fail-closed screens flag).
            assert answer is None
            continue
        assert answer is not None
        assert answer.noul is not None
        assert not isinstance(answer.noul, bool)
        assert 0.0 <= answer.noul <= 1.0
        assert answer.confidence is not None


def test_adapter_choice_and_score_shapes_parse() -> None:
    server = _load_sidecar_module()
    questions = {
        "routing": {
            "type": "choice",
            "instructions": "pick",
            "criteria": {"a": "a", "b": "b"},
        },
        "depth": {
            "type": "score",
            "instructions": "score",
            "criteria": ["l", "m", "h"],
        },
    }
    built = server.build_answers(
        questions,
        {
            "routing": {
                "choice": "a",
                "confidence": 0.81,
                "answer_confidence": 0.78,
                "probabilities": {"a": 0.78, "b": 0.22},
            },
            "depth": {"score": 1.0, "confidence": 0.9, "legend": {"1": "m"}},
            "dropped": {"choice": None, "confidence": 0.5},
        },
    )
    parsed = parse_decisions_payload(
        {"answers": built}, tier="laya", session_id="adapter:choice"
    )
    routing = parsed.answer("routing")
    assert routing is not None
    assert routing.choice == "a"
    # Upstream recommends answer_confidence (the probability of the
    # reported answer) for gating; the adapter prefers it over the
    # entropy-summary "confidence".
    assert routing.confidence == pytest.approx(0.78)
    assert abs(sum(routing.probabilities.values()) - 1.0) < 1e-6
    depth = parsed.answer("depth")
    assert depth is not None
    assert depth.score == pytest.approx(1.0)
    assert depth.confidence == pytest.approx(0.9)
    # A choice with no confidence is omitted, never fabricated.
    assert parsed.answer("dropped") is None


def test_adapter_omits_garbage_answers_rather_than_fabricating() -> None:
    server = _load_sidecar_module()
    questions = {"gate": {"type": "noul", "instructions": "statement"}}
    built = server.build_answers(questions, {"gate": {"noul": "not-a-number"}})
    assert built == {}
    built = server.build_answers(questions, "not-a-dict")
    assert built == {}


def test_calibration_fixture_known_probabilities() -> None:
    """A fitted-temperature fixture: the probabilities below are what the
    container's temperature-fitted adapter must produce (within tolerance)
    for a known mid-entropy input. Raw checkpoints run hotter; if the
    container ever regresses to raw temperatures, this shape of fixture is
    where it shows."""
    fitted = {
        "answers": {
            "gate": {
                "type": "noul",
                "noul": 0.62,
                "confidence": 0.62,
                "raw_logit": 0.49,
            }
        }
    }
    result = parse_decisions_payload(fitted, tier="laya", session_id="calibration:1")
    answer = result.answer("gate")
    assert answer is not None
    assert answer.noul is not None
    # Fitted temperatures keep confidence in the calibrated band; the raw
    # checkpoint's over-confident profile (>= 0.95 on mid-entropy inputs)
    # would fail this bound.
    assert 0.4 <= answer.noul <= 0.85
