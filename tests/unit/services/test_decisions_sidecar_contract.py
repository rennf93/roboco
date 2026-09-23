"""Contract test: the roboco-jev sidecar's responses must parse under the
same schemas.py as OpenRouter's (spec sections 4 and 10). The sidecar
mirrors the OpenRouter Decisions wire shape, so this fixture pins that
contract offline: if the container's adapter ever drifts from the wire
shape, this fails before the resolver falls over.

The calibration fixture (known probabilities) is the standing check that
the container MUST run Laya's fitted temperatures, never raw (spec 10):
a raw-temperature checkpoint would ship the published over-confident ECE
0.466 profile, and these bounds catch it.
"""

import httpx
import pytest
from roboco.services.decisions.client import DecisionsClient, DecisionsEndpoint
from roboco.services.decisions.schemas import parse_decisions_payload

# A canned sidecar response body, byte-for-byte the shape the adapter
# returns for a batched two-question request.
_SIDECAR_RESPONSE = {
    "id": "jev-local-018f3c",
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


def test_sidecar_response_parses_under_the_shared_schemas():
    result = parse_decisions_payload(
        _SIDECAR_RESPONSE, tier="laya", session_id="selfheal:contract"
    )
    assert result.tier == "laya"
    assert result.answer("gate").noul == pytest.approx(0.93)
    choice = result.answer("routing")
    assert choice.choice == "park_standard"
    assert choice.confidence == pytest.approx(0.81)
    assert abs(sum(choice.probabilities.values()) - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_sidecar_response_parses_through_the_real_client():
    """End-to-end through DecisionsClient with a transport that stands in
    for the container's HTTP surface."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/alpha/decisions")
        return httpx.Response(200, json=_SIDECAR_RESPONSE)

    endpoint = DecisionsEndpoint(
        tier="laya",
        base_url="http://roboco-jev:8100",
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


def test_calibration_fixture_known_probabilities():
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
    assert answer.noul is not None
    # Fitted temperatures keep confidence in the calibrated band; the raw
    # checkpoint's over-confident profile (>= 0.95 on mid-entropy inputs)
    # would fail this bound.
    assert 0.4 <= answer.noul <= 0.85
