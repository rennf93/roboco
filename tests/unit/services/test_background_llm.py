"""Background LLM client: provider-following backend choice, Ollama->Haiku
fallback, the HTTP 402 cooldown, and the locked-down Haiku invocation.

Fakes sit only at the network seams (tests/unit/services/background_llm_fakes.py);
the client's own code runs for real.
"""

from __future__ import annotations

import asyncio
import dataclasses
from http import HTTPStatus
from typing import Any

import httpx
import pytest
from claude_agent_sdk._internal.transport.subprocess_cli import (
    SubprocessCLITransport,
)
from roboco.config import Settings, settings
from roboco.models.runtime import MODEL_MAP
from roboco.services import background_llm
from roboco.services.background_llm import (
    BackgroundPrompt,
    generate_background_reply,
    generate_background_text,
    locked_down_haiku_options,
)
from tests.unit.services.background_llm_fakes import (
    FakeHaiku,
    FakeOllama,
    follow_routing_mode,
    record_client_logs,
    serve_fake_haiku,
    serve_fake_ollama,
)

_FLASH_MODEL = "glm-5.3-flash:cloud"
_ANSWER_BUDGET = 200
_THINKING_HEADROOM = 2048
_SHORT_TIMEOUT_SECONDS = 0.05
_SLOW_OLLAMA_SECONDS = 1.0
_TEST_COOLDOWN_SECONDS = 1.0
_PAST_TEST_COOLDOWN_SECONDS = 1.1
_CLAUDE_RATE_LIMITED = 429
_LOW_TEMPERATURE = 0.1
_OLLAMA_CONTEXT_TOKENS = 8192
_TWO_CALLS = 2

_BASE_PROMPT = BackgroundPrompt(
    purpose="test.purpose",
    user_prompt="Summarize the citations.",
    max_tokens=_ANSWER_BUDGET,
    timeout_seconds=5.0,
)


def _prompt(**overrides: Any) -> BackgroundPrompt:
    return dataclasses.replace(_BASE_PROMPT, **overrides)


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> FakeOllama:
    fake = FakeOllama()
    serve_fake_ollama(monkeypatch, fake)
    return fake


@pytest.fixture
def haiku(monkeypatch: pytest.MonkeyPatch) -> FakeHaiku:
    fake = FakeHaiku()
    serve_fake_haiku(monkeypatch, fake)
    return fake


# --- backend choice follows the routing mode --------------------------------


def test_the_ollama_model_defaults_to_the_flash_tier() -> None:
    assert Settings.model_fields["local_llm_model"].default == _FLASH_MODEL


async def test_ollama_mode_answers_from_the_flash_model(
    monkeypatch: pytest.MonkeyPatch, ollama: FakeOllama, haiku: FakeHaiku
) -> None:
    follow_routing_mode(monkeypatch, "ollama")
    monkeypatch.setattr(settings, "local_llm_model", _FLASH_MODEL)

    reply = await generate_background_reply(_prompt())

    assert reply.text == "answer from ollama"
    assert (reply.backend, reply.model, reply.failures) == ("ollama", _FLASH_MODEL, ())
    assert [request["model"] for request in ollama.requests] == [_FLASH_MODEL]
    assert haiku.calls == []


async def test_ollama_request_carries_the_call_sites_settings(
    monkeypatch: pytest.MonkeyPatch, ollama: FakeOllama
) -> None:
    follow_routing_mode(monkeypatch, "hummin")

    await generate_background_text(
        _prompt(
            system_prompt="SYSTEM",
            temperature=_LOW_TEMPERATURE,
            ollama_num_ctx=_OLLAMA_CONTEXT_TOKENS,
        )
    )

    (request,) = ollama.requests
    assert request["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "Summarize the citations."},
    ]
    assert request["max_tokens"] == _ANSWER_BUDGET
    assert request["temperature"] == _LOW_TEMPERATURE
    assert request["options"] == {"num_ctx": _OLLAMA_CONTEXT_TOKENS}


async def test_anthropic_mode_never_calls_ollama(
    monkeypatch: pytest.MonkeyPatch, ollama: FakeOllama, haiku: FakeHaiku
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")

    reply = await generate_background_reply(_prompt())

    assert reply.text == "answer from haiku"
    assert (reply.backend, reply.model) == ("claude", MODEL_MAP["haiku"])
    assert ollama.requests == []
    assert len(haiku.calls) == 1


async def test_anthropic_mode_with_haiku_down_still_never_calls_ollama(
    monkeypatch: pytest.MonkeyPatch, ollama: FakeOllama
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")
    serve_fake_haiku(monkeypatch, FakeHaiku(answer=None))

    reply = await generate_background_reply(_prompt())

    assert reply.text is None
    assert [failure.backend for failure in reply.failures] == ["claude"]
    assert ollama.requests == []


@pytest.mark.usefixtures("haiku")
async def test_an_unreadable_mode_tries_ollama_then_haiku(ollama: FakeOllama) -> None:
    # conftest makes the routing table unreadable for every test.
    ollama.status = HTTPStatus.SERVICE_UNAVAILABLE

    reply = await generate_background_reply(_prompt())

    assert reply.backend == "claude"
    assert len(ollama.requests) == 1


# --- Ollama unavailable -> Haiku ---------------------------------------------


@pytest.mark.parametrize(
    ("ollama_failure", "expected_reason", "expected_status"),
    [
        (
            FakeOllama(status=HTTPStatus.PAYMENT_REQUIRED),
            "HTTP 402",
            HTTPStatus.PAYMENT_REQUIRED,
        ),
        (
            FakeOllama(status=HTTPStatus.SERVICE_UNAVAILABLE),
            "HTTP 503",
            HTTPStatus.SERVICE_UNAVAILABLE,
        ),
        (
            FakeOllama(status=HTTPStatus.TOO_MANY_REQUESTS),
            "HTTP 429",
            HTTPStatus.TOO_MANY_REQUESTS,
        ),
        (
            FakeOllama(error=httpx.ConnectError("connection refused")),
            "connection failed: ConnectError",
            None,
        ),
        (FakeOllama(error=httpx.ReadTimeout("read timed out")), "timed out", None),
        (FakeOllama(delay_seconds=_SLOW_OLLAMA_SECONDS), "timed out after 0.05s", None),
        (FakeOllama(content="   "), "empty reply", None),
    ],
    ids=["402", "503", "429", "connect-error", "http-timeout", "slow", "empty"],
)
async def test_ollama_unavailable_falls_back_to_haiku(
    monkeypatch: pytest.MonkeyPatch,
    haiku: FakeHaiku,
    ollama_failure: FakeOllama,
    expected_reason: str,
    expected_status: int | None,
) -> None:
    follow_routing_mode(monkeypatch, "ollama")
    serve_fake_ollama(monkeypatch, ollama_failure)

    reply = await generate_background_reply(
        _prompt(timeout_seconds=_SHORT_TIMEOUT_SECONDS)
    )

    assert reply.text == "answer from haiku"
    assert reply.backend == "claude"
    (failure,) = reply.failures
    assert failure.backend == "ollama"
    assert expected_reason in failure.reason
    assert failure.status == expected_status
    assert len(haiku.calls) == 1


@pytest.mark.usefixtures("haiku")
async def test_every_fallback_is_logged_with_purpose_backend_and_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follow_routing_mode(monkeypatch, "ollama")
    serve_fake_ollama(monkeypatch, FakeOllama(status=HTTPStatus.PAYMENT_REQUIRED))
    logs = record_client_logs(monkeypatch)

    await generate_background_reply(_prompt(purpose="rag.synthesis"))

    (event,) = logs.fallback_events()
    assert event["purpose"] == "rag.synthesis"
    assert event["mode"] == "ollama"
    assert event["backend"] == "ollama"
    assert event["status"] == HTTPStatus.PAYMENT_REQUIRED
    assert "HTTP 402" in event["reason"]
    assert event["next_backend"] == "claude"


async def test_when_every_backend_fails_the_reply_has_no_text_and_both_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follow_routing_mode(monkeypatch, "ollama")
    serve_fake_ollama(monkeypatch, FakeOllama(status=HTTPStatus.PAYMENT_REQUIRED))
    serve_fake_haiku(
        monkeypatch,
        FakeHaiku(
            answer="Claude usage limit reached",
            is_error=True,
            api_error_status=_CLAUDE_RATE_LIMITED,
        ),
    )
    logs = record_client_logs(monkeypatch)

    reply = await generate_background_reply(_prompt())

    assert reply.text is None
    assert [(f.backend, f.status) for f in reply.failures] == [
        ("ollama", HTTPStatus.PAYMENT_REQUIRED),
        ("claude", _CLAUDE_RATE_LIMITED),
    ]
    assert "Claude usage limit reached" in reply.describe_failures()
    assert [event["next_backend"] for event in logs.fallback_events()] == [
        "claude",
        None,
    ]
    assert await generate_background_text(_prompt()) is None


# --- the HTTP 402 cooldown ----------------------------------------------------


@pytest.mark.usefixtures("haiku")
async def test_a_402_skips_ollama_for_the_cooldown_then_tries_it_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follow_routing_mode(monkeypatch, "ollama")
    monkeypatch.setattr(
        settings,
        "background_llm_ollama_no_credit_cooldown_seconds",
        _TEST_COOLDOWN_SECONDS,
    )
    ollama = FakeOllama(status=HTTPStatus.PAYMENT_REQUIRED)
    serve_fake_ollama(monkeypatch, ollama)

    first = await generate_background_reply(_prompt())
    during_cooldown = await generate_background_reply(_prompt())

    assert len(ollama.requests) == 1
    assert first.backend == during_cooldown.backend == "claude"
    assert "no-credit cooldown" in during_cooldown.failures[0].reason

    ollama.status = HTTPStatus.OK
    await asyncio.sleep(_PAST_TEST_COOLDOWN_SECONDS)
    after_cooldown = await generate_background_reply(_prompt())

    assert len(ollama.requests) == _TWO_CALLS
    assert after_cooldown.backend == "ollama"


@pytest.mark.usefixtures("haiku")
async def test_a_503_does_not_start_the_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follow_routing_mode(monkeypatch, "ollama")
    ollama = FakeOllama(status=HTTPStatus.SERVICE_UNAVAILABLE)
    serve_fake_ollama(monkeypatch, ollama)

    await generate_background_reply(_prompt())
    await generate_background_reply(_prompt())

    assert len(ollama.requests) == _TWO_CALLS


# --- the routing-mode cache ---------------------------------------------------


@pytest.mark.usefixtures("haiku")
async def test_the_routing_mode_is_read_once_per_cache_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[str] = []

    async def _counting_read() -> str:
        reads.append("read")
        return "anthropic"

    monkeypatch.setattr(background_llm, "read_routing_mode", _counting_read)

    await generate_background_text(_prompt())
    await generate_background_text(_prompt())
    assert len(reads) == 1

    monkeypatch.setattr(settings, "background_llm_mode_cache_seconds", 0.0)
    await generate_background_text(_prompt())
    assert len(reads) == _TWO_CALLS


@pytest.mark.usefixtures("haiku")
async def test_a_failed_mode_read_keeps_the_last_mode_known(
    monkeypatch: pytest.MonkeyPatch, ollama: FakeOllama
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")
    await generate_background_text(_prompt())

    async def _database_down() -> str:
        raise ConnectionRefusedError("database down")

    monkeypatch.setattr(background_llm, "read_routing_mode", _database_down)
    monkeypatch.setattr(settings, "background_llm_mode_cache_seconds", 0.0)
    reply = await generate_background_reply(_prompt())

    assert reply.backend == "claude"
    assert ollama.requests == []


# --- the locked-down Haiku call -----------------------------------------------


def test_haiku_options_render_a_single_locked_down_turn() -> None:
    options = locked_down_haiku_options(_prompt(system_prompt="SYSTEM"))
    transport = SubprocessCLITransport(prompt="unused", options=options)
    transport._cli_path = "claude"

    command = transport._build_command()

    assert command[command.index("--model") + 1] == MODEL_MAP["haiku"]
    assert command[command.index("--system-prompt") + 1] == "SYSTEM"
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--max-turns") + 1] == "1"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--effort") + 1] == "low"
    for flag in (
        "--strict-mcp-config",
        "--setting-sources=",
        "--safe-mode",
        "--no-session-persistence",
    ):
        assert flag in command
    assert "--mcp-config" not in command
    assert "--plugin-dir" not in command
    assert "--settings" not in command


def test_haiku_options_keep_untrusted_text_verbatim_in_a_scratch_directory() -> None:
    options = locked_down_haiku_options(_prompt())

    assert options.verbatim_prompts is True
    assert options.hooks is None
    assert options.plugins == []
    assert options.cwd == background_llm._scratch_working_directory()
    assert options.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(
        _ANSWER_BUDGET + _THINKING_HEADROOM
    )


async def test_haiku_gets_the_prompt_and_the_system_prompt(
    monkeypatch: pytest.MonkeyPatch, haiku: FakeHaiku
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")

    await generate_background_text(_prompt(system_prompt="SYSTEM"))

    ((prompt, options),) = haiku.calls
    assert prompt == "Summarize the citations."
    assert options.system_prompt == "SYSTEM"
    assert options.model == MODEL_MAP["haiku"]


@pytest.mark.parametrize("variable", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"])
async def test_haiku_refuses_to_run_on_a_metered_credential(
    monkeypatch: pytest.MonkeyPatch, haiku: FakeHaiku, variable: str
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")
    monkeypatch.setenv(variable, "sk-ant-not-the-login")

    reply = await generate_background_reply(_prompt())

    assert reply.text is None
    assert variable in reply.failures[0].reason
    assert haiku.calls == []


async def test_a_slow_haiku_call_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    follow_routing_mode(monkeypatch, "anthropic")

    async def _never_answers(**_kwargs: object) -> object:
        await asyncio.sleep(_SLOW_OLLAMA_SECONDS)
        yield None

    monkeypatch.setattr("claude_agent_sdk.query", _never_answers)

    reply = await generate_background_reply(
        _prompt(timeout_seconds=_SHORT_TIMEOUT_SECONDS)
    )

    assert reply.text is None
    assert "timed out after 0.05s" in reply.failures[0].reason
