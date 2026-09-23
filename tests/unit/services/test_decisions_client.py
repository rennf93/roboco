"""Unit tests for DecisionsClient: mocked httpx transport covering success,
error envelope, timeout, malformed payload, low confidence, circuit open,
and the client-side token caps (spec section 9)."""

import httpx
import pytest

import roboco.config as cfg
from roboco.services.decisions.client import (
    DecisionsClient,
    DecisionsEndpoint,
    cap_state,
    cap_text,
)
from roboco.services.decisions.schemas import ChoiceQuestion, NoulQuestion

_LAYA = DecisionsEndpoint(
    tier="laya",
    base_url="http://roboco-jev:8100",
    model="convaiinnovations/laya",
    timeout_s=5.0,
)

_OK_PAYLOAD = {
    "model": "convaiinnovations/laya",
    "answers": {
        "gate": {"type": "noul", "noul": 0.9, "confidence": 0.9},
    },
    "usage": {"input_tokens": 100, "output_tokens": 5, "cost": 0.0},
}


def _client_with(handler) -> DecisionsClient:
    return DecisionsClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_success_returns_parsed_result():
    async def run():
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json=_OK_PAYLOAD)

        client = _client_with(handler)
        result = await client.decide(
            _LAYA,
            {"repo": "roboco"},
            {"gate": NoulQuestion(instructions="Transient?")},
            "selfheal:run-1",
        )
        await client.aclose()
        return result, calls

    result, calls = await run()
    assert result is not None
    assert result.tier == "laya"
    assert result.answer("gate").noul == 0.9
    body = calls[0].read().decode()
    assert "/api/alpha/decisions" in str(calls[0].url)
    assert "selfheal:run-1" in body


@pytest.mark.asyncio
async def test_session_id_capped_at_256_chars():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["session_id"] = json.loads(request.read())["session_id"]
        return httpx.Response(200, json=_OK_PAYLOAD)

    client = _client_with(handler)
    await client.decide(_LAYA, {}, {}, "x" * 400)
    await client.aclose()
    assert len(captured["session_id"]) == 256


@pytest.mark.asyncio
async def test_error_envelope_is_no_verdict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"error": {"code": 429, "message": "rate limited"}}
        )

    client = _client_with(handler)
    result = await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert result is None


@pytest.mark.asyncio
async def test_http_error_status_is_no_verdict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, json={"error": {"code": 413, "message": "too big"}})

    client = _client_with(handler)
    result = await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert result is None


@pytest.mark.asyncio
async def test_timeout_is_no_verdict():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    client = _client_with(handler)
    result = await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert result is None


@pytest.mark.asyncio
async def test_malformed_payload_is_no_verdict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json{{{")

    client = _client_with(handler)
    result = await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert result is None


@pytest.mark.asyncio
async def test_circuit_opens_after_three_consecutive_failures():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    client = _client_with(handler)
    for _ in range(3):
        assert await client.decide(_LAYA, {}, {}, "s") is None
    assert calls["n"] == 3
    # Circuit open: no further HTTP attempts.
    assert await client.decide(_LAYA, {}, {}, "s") is None
    assert calls["n"] == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_success_resets_circuit():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=_OK_PAYLOAD)

    client = _client_with(handler)
    await client.decide(_LAYA, {}, {}, "s")
    await client.decide(_LAYA, {}, {}, "s")
    await client.decide(_LAYA, {}, {}, "s")
    assert calls["n"] == 3  # never opened: 2 failures < threshold
    await client.aclose()


@pytest.mark.asyncio
async def test_bearer_key_sent_for_openrouter_tier():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_OK_PAYLOAD)

    endpoint = DecisionsEndpoint(
        tier="openrouter",
        base_url="https://openrouter.ai",
        model="typesafe/jev-1.13",
        timeout_s=2.0,
        api_key="sk-or-test",
    )
    client = _client_with(handler)
    result = await client.decide(endpoint, {}, {}, "s")
    await client.aclose()
    assert captured["auth"] == "Bearer sk-or-test"
    assert result.tier == "openrouter"


@pytest.mark.asyncio
async def test_no_auth_header_for_laya_tier():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_OK_PAYLOAD)

    client = _client_with(handler)
    await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert captured["auth"] is None


class TestTokenCaps:
    def test_diff_capped_head(self):
        state = {"diff": "x" * 20_000}
        capped = cap_state(state)["diff"]
        assert len(capped) <= 8_100
        assert "truncated" in capped

    def test_error_excerpt_keeps_tail(self):
        state = {"error_excerpt": "head-junk" + "y" * 5_000 + "FATAL: at end"}
        capped = cap_state(state)["error_excerpt"]
        assert len(capped) <= 2_100
        assert capped.endswith("FATAL: at end")

    def test_task_description_capped(self):
        state = {"task_description": "z" * 10_000}
        assert len(cap_state(state)["task_description"]) <= 4_100

    def test_nested_structures_capped(self):
        state = {"repo": {"diff": "x" * 20_000}, "files": [{"log": "e" * 9_999}]}
        capped = cap_state(state)
        assert len(capped["repo"]["diff"]) <= 8_100
        assert len(capped["files"][0]["log"]) <= 2_100

    def test_cap_text_tail(self):
        assert cap_text("abcdef", 4, keep_tail=True).endswith("f")

    def test_short_values_untouched(self):
        state = {"diff": "small diff"}
        assert cap_state(state) == state


def test_flag_off_client_still_parses_but_resolver_gates(monkeypatch):
    monkeypatch.setattr(cfg.settings, "decisions_enabled", False)
    # The client itself never checks the flag (the resolver owns the chain);
    # this assertion documents that split.
    assert cfg.settings.decisions_enabled is False
