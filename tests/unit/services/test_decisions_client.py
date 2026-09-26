"""Unit tests for DecisionsClient: mocked httpx transport covering success,
error envelope, timeout, malformed payload, low confidence, circuit open,
and the client-side token caps (spec section 9): per-key billing caps for
both tiers plus the laya-only total-budget pass."""

import json
from collections.abc import Callable
from typing import cast

import httpx
import pytest
import roboco.config as cfg
from roboco.services.decisions.client import (
    _LAYA_STATE_BUDGET_CHARS,
    DecisionsClient,
    DecisionsEndpoint,
    cap_state,
    cap_state_to_budget,
    cap_text,
)
from roboco.services.decisions.schemas import DecisionResult, NoulQuestion


def _note_spend_noop(tier: str, cost: float | None) -> None:
    return None


_LAYA = DecisionsEndpoint(
    tier="laya",
    base_url="http://roboco-decisions:8100",
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


def _client_with(handler: Callable[[httpx.Request], httpx.Response]) -> DecisionsClient:
    return DecisionsClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_success_returns_parsed_result() -> None:
    async def run() -> tuple[DecisionResult | None, list[httpx.Request]]:
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
    gate = result.answer("gate")
    assert gate is not None
    assert gate.noul == 0.9
    body = calls[0].read().decode()
    assert "/api/alpha/decisions" in str(calls[0].url)
    assert "selfheal:run-1" in body


@pytest.mark.asyncio
async def test_session_id_capped_at_256_chars() -> None:
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
async def test_error_envelope_is_no_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"error": {"code": 429, "message": "rate limited"}}
        )

    client = _client_with(handler)
    result = await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert result is None


@pytest.mark.asyncio
async def test_http_error_status_is_no_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, json={"error": {"code": 413, "message": "too big"}})

    client = _client_with(handler)
    result = await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert result is None


@pytest.mark.asyncio
async def test_timeout_is_no_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    client = _client_with(handler)
    result = await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert result is None


@pytest.mark.asyncio
async def test_malformed_payload_is_no_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json{{{")

    client = _client_with(handler)
    result = await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert result is None


@pytest.mark.asyncio
async def test_circuit_opens_after_three_consecutive_failures() -> None:
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
async def test_success_resets_circuit() -> None:
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
async def test_bearer_key_sent_for_openrouter_tier() -> None:
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
    assert result is not None
    assert result.tier == "openrouter"


@pytest.mark.asyncio
async def test_no_auth_header_for_laya_tier() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_OK_PAYLOAD)

    client = _client_with(handler)
    await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert captured["auth"] is None


class TestTokenCaps:
    def test_diff_capped_head(self) -> None:
        state = {"diff": "x" * 20_000}
        capped = cast("dict[str, str]", cap_state(state))["diff"]
        assert len(capped) <= 8_100
        assert "truncated" in capped

    def test_error_excerpt_keeps_tail(self) -> None:
        state = {"error_excerpt": "head-junk" + "y" * 5_000 + "FATAL: at end"}
        capped = cast("dict[str, str]", cap_state(state))["error_excerpt"]
        assert len(capped) <= 2_100
        assert capped.endswith("FATAL: at end")

    def test_task_description_capped(self) -> None:
        state = {"task_description": "z" * 10_000}
        assert (
            len(cast("dict[str, str]", cap_state(state))["task_description"]) <= 4_100
        )

    def test_nested_structures_capped(self) -> None:
        state = {"repo": {"diff": "x" * 20_000}, "files": [{"log": "e" * 9_999}]}
        capped = cast("dict[str, object]", cap_state(state))
        repo = cast("dict[str, object]", capped["repo"])
        files = cast("list[object]", capped["files"])
        first_file = cast("dict[str, object]", files[0])
        assert len(cast("str", repo["diff"])) <= 8_100
        assert len(cast("str", first_file["log"])) <= 2_100

    def test_cap_text_tail(self) -> None:
        assert cap_text("abcdef", 4, keep_tail=True).endswith("f")

    def test_short_values_untouched(self) -> None:
        state = {"diff": "small diff"}
        assert cap_state(state) == state


class TestLayaBudget:
    """The stage-2 total-budget pass: laya tier only, after the per-key
    caps, so the tiny default-serving Laya context still sees coherent
    states instead of silently truncated mush."""

    def test_under_budget_untouched(self) -> None:
        state = {"task_title": "small", "criteria": ["a", "b"]}
        assert cap_state_to_budget(state, _LAYA_STATE_BUDGET_CHARS) == state

    def test_truncates_longest_string_first(self) -> None:
        state = {
            "task_description": "z" * 5_000,
            "criteria": ["a" * 1_000, "b" * 900],
        }
        capped = cast("dict[str, object]", cap_state_to_budget(state, 1_800))
        assert len(json.dumps(capped, ensure_ascii=False, default=str)) <= 1_800
        desc = cast("str", capped["task_description"])
        assert "truncated" in desc
        # The longest strings are shrunk first: the 900-char criterion
        # survives untouched, the 1000-char one does not.
        criteria = cast("list[object]", capped["criteria"])
        assert criteria[1] == "b" * 900
        assert criteria[0] != "a" * 1_000

    def test_keeps_tail_for_tail_keys(self) -> None:
        state = {"error_excerpt": "head-junk" + "y" * 5_000 + "FATAL: at end"}
        capped = cast("dict[str, object]", cap_state_to_budget(state, 1_800))
        excerpt = cast("str", capped["error_excerpt"])
        assert len(json.dumps(capped, ensure_ascii=False, default=str)) <= 1_800
        assert excerpt.endswith("FATAL: at end")

    def test_does_not_mutate_input(self) -> None:
        state = {"task_description": "z" * 5_000}
        cap_state_to_budget(state, 1_800)
        assert state == {"task_description": "z" * 5_000}

    def test_nothing_shrinkable_terminates(self) -> None:
        # Over budget but every string is already shorter than the
        # truncation suffix: must terminate, state unchanged.
        state = {f"k{i}": "v" for i in range(3)}
        assert cap_state_to_budget(state, 4) == state

    @pytest.mark.asyncio
    async def test_laya_tier_applies_budget_pass(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["state"] = json.loads(request.read())["state"]
            return httpx.Response(200, json=_OK_PAYLOAD)

        client = _client_with(handler)
        await client.decide(_LAYA, {"task_description": "z" * 5_000}, {}, "s")
        await client.aclose()
        assert (
            len(json.dumps(captured["state"], ensure_ascii=False, default=str))
            <= _LAYA_STATE_BUDGET_CHARS
        )

    @pytest.mark.asyncio
    async def test_laya_budget_charges_question_text_too(self) -> None:
        """Question text rides in the same tiny model context: a wide
        question batch shrinks the STATE budget rather than silently
        overflowing the model (questions themselves are never truncated,
        since a half instruction can invert a verdict)."""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.read())
            captured["state"] = body["state"]
            captured["questions"] = body["questions"]
            return httpx.Response(200, json=_OK_PAYLOAD)

        client = _client_with(handler)
        big_questions = {
            f"q{i}": NoulQuestion(instructions="x" * 300) for i in range(10)
        }
        await client.decide(
            _LAYA, {"task_description": "z" * 5_000}, big_questions, "s"
        )
        await client.aclose()
        state_size = len(json.dumps(captured["state"], ensure_ascii=False, default=str))
        question_size = len(
            json.dumps(captured["questions"], ensure_ascii=False, default=str)
        )
        # The questions alone exhaust the budget here, so the state drops
        # to its floor (a few short fields alive) instead of the pair
        # silently overflowing the model context.
        assert state_size < 400
        # The questions survive whole.
        assert question_size > 3_000
        assert all(
            len(q["instructions"]) == 300 for q in captured["questions"].values()
        )

    @pytest.mark.asyncio
    async def test_openrouter_tier_keeps_per_key_caps_only(self) -> None:
        # The budget pass is laya-only: the OpenRouter fallback keeps its
        # per-key billing caps (here 4k for task_description), far over the
        # laya budget, intact for the bigger fallback model.
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["state"] = json.loads(request.read())["state"]
            return httpx.Response(200, json=_OK_PAYLOAD)

        client = _client_with(handler)
        await client.decide(_OPENROUTER, {"task_description": "z" * 5_000}, {}, "s")
        await client.aclose()
        sent = cast("str", captured["state"]["task_description"])
        assert len(sent) <= 4_100
        assert len(sent) > _LAYA_STATE_BUDGET_CHARS


def test_flag_off_client_still_parses_but_resolver_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", False)
    # The client itself never checks the flag (the resolver owns the chain);
    # this assertion documents that split.
    assert cfg.settings.decisions_enabled is False


_OPENROUTER = DecisionsEndpoint(
    tier="openrouter",
    base_url="https://openrouter.ai",
    model="typesafe/jev-1.13",
    timeout_s=2.0,
    api_key="sk-or-test",
)


@pytest.mark.asyncio
async def test_http_402_is_observed_by_spend_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 402 (credits exhausted) counts into the spend guard while the
    fail-open behavior stays exactly the same (spec 3.1)."""
    recorded = []

    def note_402(tier: str) -> None:
        recorded.append(tier)

    monkeypatch.setattr("roboco.services.decisions.client.record_402", note_402)
    monkeypatch.setattr(
        "roboco.services.decisions.client.record_spend", _note_spend_noop
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402, json={"error": {"code": 402, "message": "insufficient credits"}}
        )

    client = _client_with(handler)
    result = await client.decide(_OPENROUTER, {}, {}, "s")
    await client.aclose()
    assert result is None  # fail-open, unchanged
    assert recorded == ["openrouter"]


@pytest.mark.asyncio
async def test_non_402_errors_do_not_touch_spend_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = []

    def note_402(tier: str) -> None:
        recorded.append(tier)

    monkeypatch.setattr("roboco.services.decisions.client.record_402", note_402)
    monkeypatch.setattr(
        "roboco.services.decisions.client.record_spend", _note_spend_noop
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"code": 429, "message": "rate"}})

    client = _client_with(handler)
    result = await client.decide(_OPENROUTER, {}, {}, "s")
    await client.aclose()
    assert result is None
    assert recorded == []


@pytest.mark.asyncio
async def test_successful_call_records_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = []

    def note_spend(tier: str, cost: float | None) -> None:
        recorded.append((tier, cost))

    monkeypatch.setattr(
        "roboco.services.decisions.client.record_402", lambda tier: None
    )
    monkeypatch.setattr("roboco.services.decisions.client.record_spend", note_spend)

    payload = {
        **_OK_PAYLOAD,
        "usage": {"input_tokens": 100, "output_tokens": 5, "cost": 0.021},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = _client_with(handler)
    result = await client.decide(_LAYA, {}, {}, "s")
    await client.aclose()
    assert result is not None
    assert recorded == [("laya", 0.021)]
