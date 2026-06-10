"""Unit tests for the live intake-session relay (orchestrator side)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from roboco.services.prompter_live import (
    PrompterLiveRegistry,
    get_live_registry,
)


def test_open_get_close() -> None:
    reg = PrompterLiveRegistry()
    session = reg.open("s1", "intake-1")
    assert session.agent_id == "intake-1"
    assert reg.get("s1") is session
    reg.close("s1")
    assert reg.get("s1") is None


def test_is_alive_tracks_open_and_close() -> None:
    """is_alive backs the panel's after-reload reconnect decision."""
    reg = PrompterLiveRegistry()
    assert reg.is_alive("s1") is False  # never opened
    reg.open("s1", "intake-1")
    assert reg.is_alive("s1") is True
    reg.close("s1")
    assert reg.is_alive("s1") is False  # reaped


def test_open_is_idempotent_for_a_live_session() -> None:
    """Re-opening a live session returns the SAME object (same queue).

    Regression: a second open() that swapped in a fresh queue orphaned the SSE
    stream — the panel had already captured the first queue, so the agent's
    replies (pushed to the new queue) never reached the browser.
    """
    reg = PrompterLiveRegistry()
    first = reg.open("s1", "intake-1")
    first.queue.put_nowait({"event": "text"})  # something already queued
    second = reg.open("s1", "intake-1")
    assert second is first  # not replaced
    assert second.queue is first.queue  # same queue → stream not orphaned
    # After close, a re-open starts fresh (no stale queue carried over).
    reg.close("s1")
    third = reg.open("s1", "intake-1")
    assert third is not first
    assert third.queue.empty()


def test_push_to_unknown_or_closed_returns_false() -> None:
    reg = PrompterLiveRegistry()
    assert reg.push("nope", {"event": "text"}) is False
    reg.open("s1", "intake-1")
    assert reg.push("s1", {"event": "text"}) is True
    reg.close("s1")
    assert reg.push("s1", {"event": "text"}) is False


@pytest.mark.asyncio
async def test_stream_yields_queued_events_then_ends_on_close() -> None:
    reg = PrompterLiveRegistry()
    reg.open("s1", "intake-1")

    async def collect() -> list[dict]:
        return [ev async for ev in reg.stream("s1")]

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)  # let the stream capture the session + block on get()

    reg.push("s1", {"event": "text", "data": "hel"})
    reg.push("s1", {"event": "turn_end", "data": "{}"})
    reg.close("s1")  # sentinel ends the stream

    result = await asyncio.wait_for(task, timeout=1.0)
    assert result == [
        {"event": "text", "data": "hel"},
        {"event": "turn_end", "data": "{}"},
    ]


@pytest.mark.asyncio
async def test_stream_unknown_session_is_empty() -> None:
    reg = PrompterLiveRegistry()
    assert [ev async for ev in reg.stream("nope")] == []


@pytest.mark.asyncio
async def test_deliver_posts_to_the_container_receiver() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reg = PrompterLiveRegistry(http_client=client)
    reg.open("s1", "intake-1")

    assert await reg.deliver("s1", "hello there") is True
    assert seen["host"] == "roboco-agent-intake-1"
    assert seen["path"] == "/turn"
    assert seen["body"] == {"text": "hello there"}
    await client.aclose()


@pytest.mark.asyncio
async def test_deliver_to_unknown_or_failing_returns_false() -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    reg = PrompterLiveRegistry(http_client=client)
    assert await reg.deliver("nope", "hi") is False  # unknown session
    reg.open("s1", "intake-1")
    assert await reg.deliver("s1", "hi") is False  # 500 from container
    await client.aclose()


def test_registry_singleton() -> None:
    assert get_live_registry() is get_live_registry()
