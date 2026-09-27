"""The live CLI sessions must survive stdout lines longer than the asyncio
readline limit.

Regression (2026-09-28 intake report): hummin's --mode json emits ONE JSONL
line per protocol message, and a single long assistant message blows past
asyncio's default 64KB StreamReader limit — ``readline()`` raises
LimitOverrunError ("Separator is not found, and chunk exceed the limit"),
which used to kill the whole intake turn. The limit is raised to 16MB at
spawn and the drain loop degrades gracefully on any residual overflow."""

from __future__ import annotations

import asyncio

import pytest
from roboco.agent_sdk.grok_cli_session import GrokCliSession, _StreamAssembler
from roboco.agent_sdk.live_cli_session import HeadlessCliTurnSession
from roboco.agent_sdk.live_providers import LiveReplyAccumulator

_OVERRUN_THEN_EOF_CALLS = 2  # first call overflows, second hits EOF


class _OverrunThenEofReader:
    """readline() overflows once (the huge JSONL line), then hits EOF."""

    def __init__(self) -> None:
        self.readline_calls = 0
        self.read_sizes: list[int] = []

    async def readline(self) -> bytes:
        self.readline_calls += 1
        if self.readline_calls == 1:
            raise asyncio.LimitOverrunError("chunk exceed the limit", 0)
        return b""

    async def read(self, n: int) -> bytes:
        self.read_sizes.append(n)
        return b'{"partial": true}'


@pytest.mark.asyncio
async def test_live_drain_survives_limit_overrun() -> None:
    session = HeadlessCliTurnSession.__new__(HeadlessCliTurnSession)
    session._turn_timeout = 30.0
    reader = _OverrunThenEofReader()

    chunks = [
        chunk
        async for chunk in session._drain(reader, LiveReplyAccumulator())  # type: ignore[arg-type]
    ]

    assert [c.kind for c in chunks] == []
    assert reader.readline_calls == _OVERRUN_THEN_EOF_CALLS
    assert reader.read_sizes == [1024 * 1024]


@pytest.mark.asyncio
async def test_grok_drain_survives_limit_overrun() -> None:
    session = GrokCliSession.__new__(GrokCliSession)
    session._turn_timeout = 30.0
    reader = _OverrunThenEofReader()

    chunks = [
        chunk
        async for chunk in session._drain(reader, _StreamAssembler())  # type: ignore[arg-type]
    ]

    assert [c.kind for c in chunks] == []
    assert reader.readline_calls == _OVERRUN_THEN_EOF_CALLS
