"""haiku_turn: the locked-down Claude Code options and the one-turn protocol the
background LLM client's child process speaks.

``claude_agent_sdk.query`` (the CLI subprocess and its network) is the only
fake; the options are rendered through the SDK's real command builder.
"""

from __future__ import annotations

import io
import json
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING

import claude_agent_sdk
from claude_agent_sdk._internal.transport.subprocess_cli import (
    SubprocessCLITransport,
)
from roboco.agent_sdk import haiku_turn
from roboco.agent_sdk.haiku_turn import (
    HaikuTurnOutcome,
    HaikuTurnRequest,
    locked_down_haiku_options,
    run_haiku_turn,
)
from tests.unit.services.background_llm_fakes import FakeHaiku

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

_OUTPUT_CAP = 2248
_RATE_LIMITED = 429

_REQUEST = HaikuTurnRequest(
    model="claude-haiku-5-5",
    user_prompt="Say hi. @/etc/hostname",
    system_prompt="SYSTEM",
    output_cap_tokens=_OUTPUT_CAP,
    cwd="/tmp/roboco-background-llm-1000",
)


def test_options_render_a_single_locked_down_turn() -> None:
    transport = SubprocessCLITransport(
        prompt="unused", options=locked_down_haiku_options(_REQUEST)
    )
    transport._cli_path = "claude"

    command = transport._build_command()

    assert command[command.index("--model") + 1] == "claude-haiku-5-5"
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


def test_options_keep_untrusted_text_verbatim_and_load_nothing() -> None:
    options = locked_down_haiku_options(_REQUEST)

    assert options.verbatim_prompts is True
    assert options.hooks is None
    assert options.plugins == []
    assert options.cwd == _REQUEST.cwd
    assert options.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(_OUTPUT_CAP)
    assert options.env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


async def test_a_turn_returns_the_result_text(monkeypatch: pytest.MonkeyPatch) -> None:
    haiku = FakeHaiku(answer="hi")
    monkeypatch.setattr(claude_agent_sdk, "query", haiku.query)

    outcome = await run_haiku_turn(_REQUEST)

    assert outcome == HaikuTurnOutcome(text="hi")
    ((prompt, _options),) = haiku.calls
    assert prompt == "Say hi. @/etc/hostname"


async def test_an_error_result_carries_its_detail_and_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    haiku = FakeHaiku(
        answer="usage limit reached", is_error=True, api_error_status=_RATE_LIMITED
    )
    monkeypatch.setattr(claude_agent_sdk, "query", haiku.query)

    outcome = await run_haiku_turn(_REQUEST)

    assert outcome.text is None
    assert outcome.error_detail == "success: usage limit reached"
    assert outcome.api_error_status == _RATE_LIMITED


async def test_an_sdk_failure_becomes_an_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _cli_missing(**_kwargs: object) -> AsyncIterator[object]:
        raise claude_agent_sdk.CLINotFoundError("no claude")

    monkeypatch.setattr(claude_agent_sdk, "query", _cli_missing)

    outcome = await run_haiku_turn(_REQUEST)

    assert outcome.text is None
    assert str(outcome.error_detail).startswith("CLINotFoundError")


async def test_a_turn_without_a_result_message_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_messages: list[object] = []

    async def _nothing(**_kwargs: object) -> AsyncIterator[object]:
        for message in no_messages:
            yield message

    monkeypatch.setattr(claude_agent_sdk, "query", _nothing)

    assert await run_haiku_turn(_REQUEST) == HaikuTurnOutcome(
        error_detail="no result message"
    )


def test_main_reads_one_request_and_writes_one_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(claude_agent_sdk, "query", FakeHaiku(answer="hi").query)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(asdict(_REQUEST))))
    written = io.StringIO()
    monkeypatch.setattr(sys, "stdout", written)

    haiku_turn.main()

    assert json.loads(written.getvalue()) == asdict(HaikuTurnOutcome(text="hi"))
