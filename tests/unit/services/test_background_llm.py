"""Background LLM client: provider-following backend choice, Ollama->Haiku
fallback, the HTTP 402 cooldown, and the Haiku turn run as the login's owner.

Fakes sit only at the network, process and routing-table seams
(tests/unit/services/background_llm_fakes.py); the client's own code runs for
real. The locked-down CLI options are tested with the worker module
(tests/unit/agent_sdk/test_haiku_turn.py).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
import pytest
from roboco.config import Settings, settings
from roboco.models.runtime import MODEL_MAP
from roboco.services import background_llm
from roboco.services.background_llm import (
    BackgroundPrompt,
    ClaudeLoginOwner,
    generate_background_reply,
    generate_background_text,
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
_LOGIN_UID = 4242
_LOGIN_GID = 4343
_OTHER_UID = 5151
_STAND_IN_TIMEOUT = 1.0
_PROCESS_END_POLLS = 50
_PROCESS_END_POLL_SECONDS = 0.05

# Captured before conftest's autouse fixture swaps in a refusal for every test.
_REAL_HAIKU_TURN_IN_LOGIN_OWNER_PROCESS = (
    background_llm._haiku_turn_in_login_owner_process
)

_BASE_PROMPT = BackgroundPrompt(
    purpose="test.purpose",
    user_prompt="Summarize the citations.",
    max_tokens=_ANSWER_BUDGET,
    timeout_seconds=5.0,
)


def _prompt(**overrides: Any) -> BackgroundPrompt:
    return dataclasses.replace(_BASE_PROMPT, **overrides)


def _is_gone(pid: int) -> bool:
    """No such process, or only a zombie left for its new parent to reap."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return True
    return state == "Z"


async def _has_ended(pid: int) -> bool:
    for _ in range(_PROCESS_END_POLLS):
        if _is_gone(pid):
            return True
        await asyncio.sleep(_PROCESS_END_POLL_SECONDS)
    return False


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> FakeOllama:
    fake = FakeOllama()
    serve_fake_ollama(monkeypatch, fake)
    return fake


@pytest.fixture
def haiku(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeHaiku:
    fake = FakeHaiku()
    serve_fake_haiku(monkeypatch, fake, tmp_path)
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
    monkeypatch: pytest.MonkeyPatch, ollama: FakeOllama, tmp_path: Path
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")
    serve_fake_haiku(monkeypatch, FakeHaiku(answer=None), tmp_path)

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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
        tmp_path,
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
        background_llm, "_OLLAMA_NO_CREDIT_COOLDOWN_SECONDS", _TEST_COOLDOWN_SECONDS
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

    monkeypatch.setattr(background_llm, "_ROUTING_MODE_REREAD_SECONDS", 0.0)
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
    monkeypatch.setattr(background_llm, "_ROUTING_MODE_REREAD_SECONDS", 0.0)
    reply = await generate_background_reply(_prompt())

    assert reply.backend == "claude"
    assert ollama.requests == []


# --- the Haiku turn runs as the login's owner ---------------------------------


class _FinishedProcess:
    """What ``create_subprocess_exec`` returns, for a turn that answered."""

    pid = 999_999
    returncode = 0

    def __init__(self, outcome: dict[str, object]) -> None:
        self._stdout = json.dumps(outcome).encode()

    async def communicate(self, _input: bytes) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _login_home(tmp_path: Path) -> Path:
    (tmp_path / ".claude").mkdir()
    return tmp_path


def _stand_in_for_the_haiku_process(
    monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    """A real child process, running ``script`` instead of haiku_turn."""
    monkeypatch.setattr(
        background_llm, "_HAIKU_TURN_COMMAND", (sys.executable, "-c", script)
    )
    monkeypatch.setattr(
        background_llm,
        "_haiku_turn_in_login_owner_process",
        _REAL_HAIKU_TURN_IN_LOGIN_OWNER_PROCESS,
    )


def test_the_login_owner_is_whoever_owns_the_login_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    login_status = (_login_home(tmp_path) / ".claude").stat()
    monkeypatch.setattr(settings, "background_llm_claude_home", str(tmp_path))

    owner = background_llm._claude_login_owner()

    assert (owner.home, owner.uid, owner.gid) == (
        tmp_path,
        login_status.st_uid,
        login_status.st_gid,
    )


async def test_a_missing_login_is_a_failed_haiku_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")
    monkeypatch.setattr(settings, "background_llm_claude_home", str(tmp_path))

    reply = await generate_background_reply(_prompt())

    assert reply.text is None
    assert "no Claude Code login" in reply.failures[0].reason


def test_root_switches_to_the_login_owner_with_no_other_groups(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    owner = ClaudeLoginOwner(home=tmp_path, uid=_LOGIN_UID, gid=_LOGIN_GID)

    assert background_llm._identity_switch_for(owner) == {
        "user": _LOGIN_UID,
        "group": _LOGIN_GID,
        "extra_groups": [],
    }


def test_the_login_owner_needs_no_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: _LOGIN_UID)
    owner = ClaudeLoginOwner(home=tmp_path, uid=_LOGIN_UID, gid=_LOGIN_GID)

    assert background_llm._identity_switch_for(owner) == {}


def test_another_non_root_user_cannot_run_the_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: _OTHER_UID)
    owner = ClaudeLoginOwner(home=tmp_path, uid=_LOGIN_UID, gid=_LOGIN_GID)

    with pytest.raises(background_llm.BackendUnavailableError, match="cannot run"):
        background_llm._identity_switch_for(owner)


async def test_the_haiku_cli_is_launched_as_the_login_directory_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The orchestrator is root: the Haiku process starts as the uid:gid stat
    finds on the login, with no supplementary groups, HOME on the login's
    home, its own process group, and only the allowlisted environment."""
    follow_routing_mode(monkeypatch, "anthropic")
    login_status = (_login_home(tmp_path) / ".claude").stat()
    monkeypatch.setattr(settings, "background_llm_claude_home", str(tmp_path))
    monkeypatch.setattr(
        background_llm,
        "_haiku_turn_in_login_owner_process",
        _REAL_HAIKU_TURN_IN_LOGIN_OWNER_PROCESS,
    )
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setenv("ROBOCO_DATABASE_PASSWORD", "pw")
    launches: list[tuple[tuple[object, ...], dict[str, Any]]] = []

    async def _record_launch(*args: object, **kwargs: Any) -> _FinishedProcess:
        launches.append((args, kwargs))
        return _FinishedProcess({"text": "answer from haiku"})

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _record_launch)

    reply = await generate_background_reply(_prompt())

    assert reply.text == "answer from haiku"
    ((argv, kwargs),) = launches
    assert argv == background_llm._HAIKU_TURN_COMMAND
    assert kwargs["user"] == login_status.st_uid
    assert kwargs["group"] == login_status.st_gid
    assert kwargs["extra_groups"] == []
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["HOME"] == str(tmp_path)
    assert "ROBOCO_DATABASE_PASSWORD" not in kwargs["env"]


async def test_the_haiku_process_gets_the_request_home_cwd_and_no_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")
    monkeypatch.setattr(
        settings, "background_llm_claude_home", str(_login_home(tmp_path))
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-metered")
    monkeypatch.setenv("ROBOCO_DATABASE_PASSWORD", "pw")
    _stand_in_for_the_haiku_process(
        monkeypatch,
        "import json, os, sys\n"
        "request = json.load(sys.stdin)\n"
        "seen = {'home': os.environ.get('HOME'), 'cwd': os.getcwd(),\n"
        "        'key': os.environ.get('ANTHROPIC_API_KEY'),\n"
        "        'secret': os.environ.get('ROBOCO_DATABASE_PASSWORD'),\n"
        "        'request': request}\n"
        "json.dump({'text': json.dumps(seen)}, sys.stdout)\n",
    )

    reply = await generate_background_reply(_prompt(system_prompt="SYSTEM"))

    seen = json.loads(str(reply.text))
    assert seen["home"] == str(tmp_path)
    assert seen["key"] is None
    assert seen["secret"] is None
    assert seen["cwd"] == seen["request"]["cwd"]
    assert Path(seen["cwd"]).name == f"roboco-background-llm-{os.geteuid()}"
    assert seen["request"]["model"] == MODEL_MAP["haiku"]
    assert seen["request"]["system_prompt"] == "SYSTEM"
    assert seen["request"]["user_prompt"] == "Summarize the citations."
    assert seen["request"]["output_cap_tokens"] == _ANSWER_BUDGET + _THINKING_HEADROOM


async def test_a_timed_out_haiku_turn_kills_the_cli_with_its_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")
    monkeypatch.setattr(
        settings, "background_llm_claude_home", str(_login_home(tmp_path))
    )
    pid_file = tmp_path / "cli.pid"
    _stand_in_for_the_haiku_process(
        monkeypatch,
        "import pathlib, subprocess, sys, time\n"
        "cli = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(60)']\n"
        ")\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(cli.pid))\n"
        "time.sleep(60)\n",
    )

    reply = await generate_background_reply(_prompt(timeout_seconds=_STAND_IN_TIMEOUT))

    assert "timed out" in reply.failures[0].reason
    assert await _has_ended(int(pid_file.read_text()))


@pytest.mark.parametrize(
    ("script", "expected_reason"),
    [
        ("import sys; sys.stderr.write('boom'); sys.exit(3)", "exited 3: boom"),
        ("print('not json')", "wrote no outcome"),
    ],
)
async def test_a_broken_haiku_process_is_a_failed_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str, expected_reason: str
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")
    monkeypatch.setattr(
        settings, "background_llm_claude_home", str(_login_home(tmp_path))
    )
    _stand_in_for_the_haiku_process(monkeypatch, script)

    reply = await generate_background_reply(_prompt())

    assert reply.text is None
    assert expected_reason in reply.failures[0].reason


def test_root_hands_the_docker_made_home_to_the_login_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _login_home(tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    handed: list[tuple[object, ...]] = []
    monkeypatch.setattr(os, "chown", lambda *args, **_kwargs: handed.append(args))
    owner = ClaudeLoginOwner(home=home, uid=_LOGIN_UID, gid=_LOGIN_GID)

    background_llm._hand_home_to_login_owner(owner)

    assert handed == [(home, _LOGIN_UID, _LOGIN_GID)]


def test_root_never_hands_over_a_home_that_holds_more_than_the_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _login_home(tmp_path)
    (home / ".bashrc").write_text("")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    handed: list[tuple[object, ...]] = []
    monkeypatch.setattr(os, "chown", lambda *args, **_kwargs: handed.append(args))
    owner = ClaudeLoginOwner(home=home, uid=_LOGIN_UID, gid=_LOGIN_GID)

    with pytest.raises(background_llm.BackendUnavailableError, match="more than"):
        background_llm._hand_home_to_login_owner(owner)
    assert handed == []


async def test_haiku_gets_the_prompt_and_the_system_prompt(
    monkeypatch: pytest.MonkeyPatch, haiku: FakeHaiku
) -> None:
    follow_routing_mode(monkeypatch, "anthropic")

    await generate_background_text(_prompt(system_prompt="SYSTEM"))

    ((prompt, options),) = haiku.calls
    assert prompt == "Summarize the citations."
    assert options.system_prompt == "SYSTEM"
    assert options.model == MODEL_MAP["haiku"]
    assert options.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(
        _ANSWER_BUDGET + _THINKING_HEADROOM
    )
