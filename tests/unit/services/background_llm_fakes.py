"""Fakes at the background LLM client's seams.

Only the network, the Haiku child process and the routing table are faked:
Ollama's HTTP endpoint (an ``httpx.MockTransport`` behind the real
``httpx.AsyncClient``), and for Haiku the child process is replaced by the
same ``roboco.agent_sdk.haiku_turn.run_haiku_turn`` run in-process against a
fake ``claude_agent_sdk.query`` (the Claude Code CLI subprocess). Everything
in ``roboco.services.background_llm`` and ``haiku_turn`` around them runs for
real, against a real login directory under the test's ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import claude_agent_sdk
import httpx
from claude_agent_sdk import ResultMessage
from roboco.agent_sdk import haiku_turn
from roboco.config import settings
from roboco.services import background_llm

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import pytest
    from claude_agent_sdk import ClaudeAgentOptions
    from roboco.agent_sdk.haiku_turn import HaikuTurnOutcome, HaikuTurnRequest

_REAL_ASYNC_CLIENT = httpx.AsyncClient


@dataclass
class FakeOllama:
    """Answers Ollama's ``/chat/completions`` with a status, an error, or text."""

    status: int = HTTPStatus.OK
    content: str = "answer from ollama"
    error: Exception | None = None
    delay_seconds: float = 0.0
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        if self.status != HTTPStatus.OK:
            return httpx.Response(self.status, json={"error": f"fake {self.status}"})
        return httpx.Response(
            HTTPStatus.OK, json={"choices": [{"message": {"content": self.content}}]}
        )


@dataclass
class FakeHaiku:
    """Stands in for ``claude_agent_sdk.query``: one ``ResultMessage``."""

    answer: str | None = "answer from haiku"
    is_error: bool = False
    api_error_status: int | None = None
    errors: list[str] | None = None
    calls: list[tuple[str, ClaudeAgentOptions]] = field(default_factory=list)

    def query(
        self, *, prompt: str, options: ClaudeAgentOptions
    ) -> AsyncIterator[ResultMessage]:
        self.calls.append((prompt, options))
        return self._messages()

    async def _messages(self) -> AsyncIterator[ResultMessage]:
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=self.is_error,
            num_turns=1,
            session_id="fake-session",
            result=self.answer,
            api_error_status=self.api_error_status,
            errors=self.errors,
        )


@dataclass
class LogRecorder:
    """Records the client's structlog calls (capture_logs depends on suite-wide
    structlog cache state, see tests/unit/test_bootstrap.py)."""

    warnings: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def warning(self, event: str, **fields: Any) -> None:
        self.warnings.append((event, fields))

    def fallback_events(self) -> list[dict[str, Any]]:
        return [
            fields
            for event, fields in self.warnings
            if event.startswith("Background LLM backend failed")
        ]


def serve_fake_ollama(monkeypatch: pytest.MonkeyPatch, fake: FakeOllama) -> None:
    def _client_with_fake_transport(**kwargs: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(fake.handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client_with_fake_transport)


async def _haiku_turn_in_this_process(
    _owner: background_llm.ClaudeLoginOwner,
    request: HaikuTurnRequest,
    timeout_seconds: float,
) -> HaikuTurnOutcome:
    """The child process's work, done here: same request in, same outcome
    out, same timeout contract."""
    try:
        async with asyncio.timeout(timeout_seconds):
            return await haiku_turn.run_haiku_turn(request)
    except TimeoutError as exc:
        raise background_llm.BackendUnavailableError(
            f"timed out after {timeout_seconds:g}s"
        ) from exc


def serve_fake_haiku(
    monkeypatch: pytest.MonkeyPatch, fake: FakeHaiku, login_home: Path
) -> None:
    (login_home / ".claude").mkdir(exist_ok=True)
    monkeypatch.setattr(settings, "background_llm_claude_home", str(login_home))
    monkeypatch.setattr(claude_agent_sdk, "query", fake.query)
    monkeypatch.setattr(
        background_llm,
        "_haiku_turn_in_login_owner_process",
        _haiku_turn_in_this_process,
    )


def follow_routing_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    async def _read_mode() -> str:
        return mode

    monkeypatch.setattr(background_llm, "read_routing_mode", _read_mode)


def record_client_logs(monkeypatch: pytest.MonkeyPatch) -> LogRecorder:
    recorder = LogRecorder()
    monkeypatch.setattr(background_llm, "logger", recorder)
    return recorder
