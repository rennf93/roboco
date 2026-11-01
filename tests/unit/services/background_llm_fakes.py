"""Fakes at the background LLM client's seams.

Only the network and the routing table are faked: Ollama's HTTP endpoint
(an ``httpx.MockTransport`` behind the real ``httpx.AsyncClient``) and
``claude_agent_sdk.query`` (the Claude Code CLI subprocess). Everything in
``roboco.services.background_llm`` between them runs for real.
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
from roboco.services import background_llm

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest
    from claude_agent_sdk import ClaudeAgentOptions

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


def serve_fake_haiku(monkeypatch: pytest.MonkeyPatch, fake: FakeHaiku) -> None:
    monkeypatch.setattr(claude_agent_sdk, "query", fake.query)


def follow_routing_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    async def _read_mode() -> str:
        return mode

    monkeypatch.setattr(background_llm, "read_routing_mode", _read_mode)


def record_client_logs(monkeypatch: pytest.MonkeyPatch) -> LogRecorder:
    recorder = LogRecorder()
    monkeypatch.setattr(background_llm, "logger", recorder)
    return recorder
