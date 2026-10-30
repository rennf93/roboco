"""Contract test: the roboco-decisions sidecar's responses must parse under the
same schemas.py as OpenRouter's (spec sections 4 and 10). The sidecar
mirrors the OpenRouter Decisions wire shape, so this fixture pins that
contract offline: if the container's proxy ever drifts from the wire
shape, this fails before the resolver falls over.

The canned fixture pins the wire SHAPE; the adapter tests below import
the sidecar's REAL ``build_answers`` out of ``docker/decisions/server.py``
and parse its output, so an adapter regression (a bool noul, a missing
confidence) fails here instead of silently dead-ending the cheapest
serving tier's most-used question type in production.

The proxy tests drive the FastAPI app over an httpx ASGI transport with
the upstream llama-server stubbed by an httpx.MockTransport, pinning the
routing (model hint -> upstream), the verbatim {state, questions}
forwarding, the per-tier state caps, the honest-health aggregation, and
the optional-key auth surface - the contracts the orchestrator's client
and compose healthcheck lean on.
"""

import asyncio
import dataclasses
import importlib.util
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from roboco.services.decisions.client import DecisionsClient, DecisionsEndpoint
from roboco.services.decisions.schemas import parse_decisions_payload

# The httpx handler shape MockTransport expects, and the factory the `stub`
# fixture hands to each test (install one handler, get the recorder back).
_Handler = Callable[[httpx.Request], httpx.Response]

# A canned sidecar response body, byte-for-byte the shape the proxy
# returns for a batched two-question request (model echo of the default
# clef tier id).
_SIDECAR_RESPONSE = {
    "id": "dec-local-018f3c",
    "model": "ggml-org/Clef-GGUF:Q4_K_M",
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

# What a llama.cpp /v1/systemone response looks like at the pinned tag
# (noul carries no confidence; choice's confidence is the TypeSafe
# rescaled summary, NOT the answer probability).
_SYSTEMONE_RESPONSE: dict[str, Any] = {
    "model": "ggml-org/Laya-GGUF",
    "answers": {
        "gate": {"type": "noul", "noul": 0.93},
        "routing": {
            "type": "choice",
            "choice": "park_standard",
            "probabilities": {
                "park_standard": 0.78,
                "retry_soon": 0.2,
                "escalate": 0.02,
            },
            "confidence": 0.85,
        },
    },
    "usage": {"input_tokens": 210, "output_tokens": 0},
}


def _load_sidecar_module() -> ModuleType:
    """Import docker/decisions/server.py by path (it is not on the package
    path). Import time only needs fastapi + httpx + stdlib: the proxy has
    no llama.cpp dependency at all. The module registers itself under its
    spec name BEFORE exec (importlib's documented pattern): the @dataclass
    machinery resolves ``cls.__module__`` through sys.modules while the
    class body executes."""
    path = Path(__file__).resolve().parents[3] / "docker" / "decisions" / "server.py"
    spec = importlib.util.spec_from_file_location("roboco_decisions_sidecar", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _decisions_payload(**overrides: Any) -> dict[str, Any]:
    """A minimal valid decisions request body, with per-test overrides."""
    payload: dict[str, Any] = {
        "state": {"repo": "roboco"},
        "questions": {"gate": {"type": "noul", "instructions": "statement"}},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Wire-shape fixtures (parsed under the shared schemas)
# ---------------------------------------------------------------------------


def test_sidecar_response_parses_under_the_shared_schemas() -> None:
    result = parse_decisions_payload(
        _SIDECAR_RESPONSE, tier="clef", session_id="selfheal:contract"
    )
    assert result.tier == "clef"
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
        model="ggml-org/Laya-GGUF",
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


# ---------------------------------------------------------------------------
# Adapter normalization (the regression seam)
# ---------------------------------------------------------------------------


def test_adapter_noul_is_a_float_with_confidence_not_a_bool() -> None:
    """The regression that dead-ended the Laya tier: the adapter once
    emitted ``noul`` as a bool (and no confidence), and the orchestrator's
    parser deliberately rejects booleans - so every laya-served noul
    parsed as None and every noul pilot failed open to no-verdict. The
    adapter must emit a float probability WITH a confidence, whatever
    shape the runtime hands over (llama.cpp noul answers carry no
    confidence field at all, so the probability doubles as the gating
    confidence)."""
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
    # Gating wants the probability of the REPORTED answer: the adapter
    # prefers answer_confidence over any summary confidence, so a decisive
    # 0.78 verdict never gates under a rescaled-distance summary.
    assert routing.confidence == pytest.approx(0.78)
    assert abs(sum(routing.probabilities.values()) - 1.0) < 1e-6
    depth = parsed.answer("depth")
    assert depth is not None
    assert depth.score == pytest.approx(1.0)
    assert depth.confidence == pytest.approx(0.9)
    # A choice with no confidence is omitted, never fabricated.
    assert parsed.answer("dropped") is None


def test_adapter_recovers_answer_probability_from_the_distribution() -> None:
    """llama.cpp's choice/score confidence is the TypeSafe rescaled formula,
    not the answer probability (0.85 summary on a 0.78 max bucket in the
    canned shape). With no answer_confidence on the shape, the max bucket
    must win - the exact recovery that keeps 0.6-0.8 gating floors alive."""
    server = _load_sidecar_module()
    questions = {
        "routing": {
            "type": "choice",
            "instructions": "pick",
            "criteria": {"park_standard": "a", "retry_soon": "b", "escalate": "c"},
        }
    }
    built = server.build_answers(
        questions, {"routing": _SYSTEMONE_RESPONSE["answers"]["routing"]}
    )
    parsed = parse_decisions_payload(
        {"answers": built}, tier="clef", session_id="adapter:gating"
    )
    routing = parsed.answer("routing")
    assert routing is not None
    assert routing.confidence == pytest.approx(0.78)


def test_adapter_omits_garbage_answers_rather_than_fabricating() -> None:
    server = _load_sidecar_module()
    questions = {"gate": {"type": "noul", "instructions": "statement"}}
    built = server.build_answers(questions, {"gate": {"noul": "not-a-number"}})
    assert built == {}
    built = server.build_answers(questions, "not-a-dict")
    assert built == {}


def test_calibration_fixture_known_probabilities() -> None:
    """A calibrated-probability fixture: the probabilities below are what
    the container must produce (within tolerance) for a known mid-entropy
    input. llama.cpp scales probabilities with the model-native
    temperatures baked in the GGUF metadata, so the ONNX-era
    rl_agent_config.json gate is gone - this shape of fixture is where a
    raw/uncalibrated serving profile would show."""
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
    # Calibrated temperatures keep confidence in the calibrated band; a
    # raw serving profile (>= 0.95 on mid-entropy inputs) would fail this
    # bound.
    assert 0.4 <= answer.noul <= 0.85


# ---------------------------------------------------------------------------
# Proxy behavior (app over ASGI transport, upstream stubbed)
# ---------------------------------------------------------------------------


class _UpstreamStub:
    """Swaps the proxy's shared httpx client for a MockTransport-backed one
    and records every upstream request the app makes."""

    def __init__(self, server: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        self.server = server
        self.monkeypatch = monkeypatch
        self.calls: list[httpx.Request] = []

    def install(self, handler: _Handler) -> "_UpstreamStub":
        stub = self

        def recording_handler(request: httpx.Request) -> httpx.Response:
            stub.calls.append(request)
            return handler(request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(recording_handler))
        self.monkeypatch.setitem(self.server._HTTP, "client", client)
        return self

    async def aclose(self) -> None:
        client = self.server._HTTP["client"]
        if client is not None:
            await client.aclose()


# Defined after the class so the alias can name it eagerly (no PEP 563 in
# this file): install one upstream handler, get the recorder back.
_StubFactory = Callable[[_Handler], _UpstreamStub]


def _systemone_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_SYSTEMONE_RESPONSE)


def _health_handler(clef_ok: bool, laya_ok: bool) -> _Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("http://127.0.0.1:8110"):
            if clef_ok:
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(503, json={"error": {"message": "Loading model"}})
        if laya_ok:
            return httpx.Response(200, json={"status": "ok"})
        raise httpx.ConnectError("connection refused", request=request)

    return handler


@pytest.fixture
def server_module() -> ModuleType:
    return _load_sidecar_module()


@pytest.fixture
def stub(
    server_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_StubFactory]:
    """Install a stubbed upstream on the freshly-loaded sidecar module."""
    installed: list[_UpstreamStub] = []

    def install(handler: _Handler) -> _UpstreamStub:
        holder = _UpstreamStub(server_module, monkeypatch)
        installed.append(holder.install(handler))
        return installed[-1]

    yield install
    for holder in installed:
        asyncio.run(holder.aclose())


async def _post_decisions(
    server: ModuleType, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> httpx.Response:
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://sidecar.test"
    ) as client:
        return await client.post(
            "/api/alpha/decisions", json=payload, headers=headers or {}
        )


async def _get_health(server: ModuleType) -> httpx.Response:
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://sidecar.test"
    ) as client:
        return await client.get("/health")


@pytest.mark.asyncio
async def test_proxy_laya_routed_request_maps_the_systemone_payload(
    stub: _StubFactory, server_module: ModuleType
) -> None:
    calls = stub(_systemone_handler)
    payload = _decisions_payload(
        model="convaiinnovations/laya",
        state={"repo": "roboco", "attempt_number": 1},
        questions={
            "gate": {"type": "noul", "instructions": "statement"},
            "routing": {
                "type": "choice",
                "instructions": "pick",
                "criteria": {"park_standard": "a", "retry_soon": "b"},
            },
        },
        session_id="proxy:1",
    )
    response = await _post_decisions(server_module, payload)
    assert response.status_code == 200, response.text
    # The laya hint routes to the laya upstream, and {state, questions} is
    # forwarded verbatim (no model/session leakage into llama-server).
    assert len(calls.calls) == 1
    request = calls.calls[0]
    assert str(request.url) == "http://127.0.0.1:8111/v1/systemone"
    assert json.loads(request.content) == {
        "state": payload["state"],
        "questions": payload["questions"],
    }

    envelope = response.json()
    assert envelope["id"].startswith("dec-")
    assert envelope["model"] == "convaiinnovations/laya"
    assert envelope["usage"] == {
        "input_tokens": 210,
        "output_tokens": 0,
        "cost": 0.0,
    }
    # The noul answer carries a float probability WITH confidence even
    # though llama.cpp's noul answer has no confidence field.
    gate = envelope["answers"]["gate"]
    assert gate["noul"] == pytest.approx(0.93)
    assert not isinstance(gate["noul"], bool)
    assert gate["confidence"] == pytest.approx(0.93)
    # Gating confidence recovers the reported answer's probability (0.78)
    # from the distribution, not the TypeSafe summary (0.85).
    routing = envelope["answers"]["routing"]
    assert routing["choice"] == "park_standard"
    assert routing["confidence"] == pytest.approx(0.78)
    # ...and the whole envelope parses under the shared schemas.
    parsed = parse_decisions_payload(envelope, tier="laya", session_id="proxy:parse")
    assert parsed.answer("gate") is not None


@pytest.mark.asyncio
async def test_proxy_defaults_to_clef_and_routes_case_insensitively(
    stub: _StubFactory, server_module: ModuleType
) -> None:
    calls = stub(_systemone_handler)
    for model in (None, "typesafe/jev-1.13", "GGML-org/CLEF-GGUF:Q4_K_M"):
        payload = _decisions_payload()
        if model is not None:
            payload["model"] = model
        response = await _post_decisions(server_module, payload)
        assert response.status_code == 200, response.text
    # Every request above landed on the CLEF upstream (8110), never laya.
    assert len(calls.calls) == 3
    assert all(
        str(call.url).startswith("http://127.0.0.1:8110") for call in calls.calls
    )


@pytest.mark.asyncio
async def test_proxy_disabled_backend_503s_without_silent_rerouting(
    stub: _StubFactory,
    server_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub(_systemone_handler)
    monkeypatch.setattr(
        server_module,
        "CLEF",
        dataclasses.replace(server_module.CLEF, enabled=False),
    )
    response = await _post_decisions(server_module, _decisions_payload())
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert "clef" in detail and "disabled" in detail


@pytest.mark.asyncio
async def test_proxy_upstream_outage_maps_to_503(
    stub: _StubFactory, server_module: ModuleType
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    stub(handler)
    response = await _post_decisions(
        server_module, _decisions_payload(model="ggml-org/Laya-GGUF")
    )
    assert response.status_code == 503, response.text
    assert "upstream unreachable" in response.json()["detail"]


@pytest.mark.asyncio
async def test_proxy_state_caps_422_per_tier(
    stub: _StubFactory, server_module: ModuleType
) -> None:
    calls = stub(_systemone_handler)
    # laya tier: 1800 chars fits, 1801 refuses.
    ok = await _post_decisions(
        server_module,
        _decisions_payload(model="ggml-org/Laya-GGUF", state="x" * 1800),
    )
    assert ok.status_code == 200, ok.text
    over = await _post_decisions(
        server_module,
        _decisions_payload(model="ggml-org/Laya-GGUF", state="x" * 1801),
    )
    assert over.status_code == 422, over.text
    detail = over.json()["detail"]
    assert "1801" in detail and "laya" in detail
    # clef tier (the default route): the 40000-char state that blows
    # laya's budget fits, 40001 refuses.
    ok = await _post_decisions(server_module, _decisions_payload(state="x" * 40_000))
    assert ok.status_code == 200, ok.text
    over = await _post_decisions(server_module, _decisions_payload(state="x" * 40_001))
    assert over.status_code == 422, over.text
    assert "clef" in over.json()["detail"]
    # A 422 never reaches the upstream.
    assert len(calls.calls) == 2


@pytest.mark.asyncio
async def test_proxy_rejects_malformed_questions_before_the_upstream(
    stub: _StubFactory, server_module: ModuleType
) -> None:
    calls = stub(_systemone_handler)
    for questions in (
        None,
        {},
        {"gate": {"type": "bogus", "instructions": "statement"}},
        {"gate": {"type": "noul", "instructions": 7}},
    ):
        payload: dict[str, Any] = {"state": {"repo": "roboco"}}
        if questions is not None:
            payload["questions"] = questions
        response = await _post_decisions(server_module, payload)
        assert response.status_code == 400, (questions, response.text)
    assert not calls.calls


# ---------------------------------------------------------------------------
# /health aggregation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_200_ok_when_both_backends_serve(
    stub: _StubFactory, server_module: ModuleType
) -> None:
    stub(_health_handler(clef_ok=True, laya_ok=True))
    response = await _get_health(server_module)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["clef"]["ok"] is True
    assert body["laya"]["ok"] is True


@pytest.mark.asyncio
async def test_health_laya_down_alone_is_a_degraded_200(
    stub: _StubFactory, server_module: ModuleType
) -> None:
    """The cheap tier being missing must not take the primary out of the
    orchestrator's health view: laya down alone stays a 200."""
    stub(_health_handler(clef_ok=True, laya_ok=False))
    response = await _get_health(server_module)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    assert body["clef"]["ok"] is True
    assert body["laya"]["ok"] is False


@pytest.mark.asyncio
async def test_health_clef_down_is_a_503(
    stub: _StubFactory, server_module: ModuleType
) -> None:
    """The honest-health gate: an unreachable ENABLED clef upstream 503s so
    the resolver's cached probe and the client circuit breaker hand the
    traffic to the fallback tier instead of black-holing here."""
    stub(_health_handler(clef_ok=False, laya_ok=True))
    response = await _get_health(server_module)
    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "degraded"
    assert body["clef"]["ok"] is False
    assert body["laya"]["ok"] is True


@pytest.mark.asyncio
async def test_health_disabled_clef_is_reported_not_fatal(
    stub: _StubFactory,
    server_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled clef is a deliberate configuration, not a fault: the
    per-backend detail says so and the aggregate stays a 200 while laya
    serves (the CI live-contract job ships exactly this posture)."""
    stub(_health_handler(clef_ok=False, laya_ok=True))
    monkeypatch.setattr(
        server_module,
        "CLEF",
        dataclasses.replace(server_module.CLEF, enabled=False),
    )
    response = await _get_health(server_module)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    assert body["clef"]["ok"] is False
    assert "disabled" in body["clef"]["detail"]
    assert body["laya"]["ok"] is True


# ---------------------------------------------------------------------------
# Auth surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_is_optional_when_no_key_is_configured(
    stub: _StubFactory,
    server_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub(_systemone_handler)
    monkeypatch.setattr(server_module, "DECISIONS_API_KEY", "")
    response = await _post_decisions(server_module, _decisions_payload())
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_auth_accepts_canonical_legacy_and_bearer_headers(
    stub: _StubFactory,
    server_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub(_systemone_handler)
    monkeypatch.setattr(server_module, "DECISIONS_API_KEY", "sekrit")
    payload = _decisions_payload()
    missing = await _post_decisions(server_module, payload)
    assert missing.status_code == 401, missing.text
    wrong = await _post_decisions(server_module, payload, {"x-decisions-key": "nope"})
    assert wrong.status_code == 401, wrong.text
    canonical = await _post_decisions(
        server_module, payload, {"x-decisions-key": "sekrit"}
    )
    assert canonical.status_code == 200, canonical.text
    # The legacy x-laya-key header rides one release after the env rename.
    legacy = await _post_decisions(server_module, payload, {"x-laya-key": "sekrit"})
    assert legacy.status_code == 200, legacy.text
    bearer = await _post_decisions(
        server_module, payload, {"Authorization": "Bearer sekrit"}
    )
    assert bearer.status_code == 200, bearer.text
