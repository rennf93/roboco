"""Unit tests for the intake container entrypoint wiring helpers."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from roboco.agent_sdk.intake_driver import StreamChunk
from roboco.agent_sdk.intake_main import (
    build_receiver,
    make_message_source,
    make_relay_sink,
)


@pytest.mark.asyncio
async def test_message_source_returns_queued_then_none() -> None:
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    source = make_message_source(queue)
    await queue.put("hi")
    await queue.put(None)
    assert await source() == "hi"
    assert await source() is None  # shutdown sentinel


@pytest.mark.asyncio
async def test_relay_sink_posts_chunk_to_orchestrator() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sink = make_relay_sink("http://orch:8000", "sess-1", client)

    await sink(StreamChunk(kind="text", text="hello"))

    assert seen["url"] == "http://orch:8000/api/prompter/live/sess-1/events"
    assert seen["body"] == {"kind": "text", "text": "hello", "tool": "", "data": {}}
    await client.aclose()


@pytest.mark.asyncio
async def test_relay_sink_swallows_post_failure() -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    sink = make_relay_sink("http://orch:8000", "sess-1", client)
    await sink(StreamChunk(kind="text", text="x"))  # must not raise
    await client.aclose()


@pytest.mark.asyncio
async def test_receiver_enqueues_turn_and_validates() -> None:
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    app = build_receiver(queue)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.post("/turn", json={"text": "build a thing"})
        assert ok.status_code == HTTPStatus.OK
        assert ok.json() == {"queued": True}
        assert queue.get_nowait() == "build a thing"

        bad = await client.post("/turn", json={"text": ""})
        assert bad.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

        health = await client.get("/health")
        assert health.json() == {"status": "ok"}
