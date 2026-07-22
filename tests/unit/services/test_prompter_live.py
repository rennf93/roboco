"""Unit tests for the live intake-session relay (orchestrator side)."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
import roboco.services.prompter_live as pl
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


def test_idle_session_ids_reaps_only_abandoned_chats() -> None:
    reg = PrompterLiveRegistry()
    old = reg.open("idle", "intake-1")
    old.last_activity = time.monotonic() - 4000  # silent for >1h
    reg.open("fresh", "intake-1")  # just opened — active
    parked = reg.open("parked", "intake-1")  # board-review parked
    parked.last_activity = time.monotonic() - 4000
    parked.task_id = "task-9"
    reg.open("done", "secretary-1")
    reg.close("done")  # closed sessions excluded

    idle = dict(reg.idle_session_ids(1800))
    assert "idle" in idle and idle["idle"] == "intake-1"  # abandoned -> reaped
    assert "fresh" not in idle  # active -> kept
    assert "parked" not in idle  # board-review parked -> exempt
    assert "done" not in idle  # closed -> excluded
    # Disabled when threshold <= 0.
    assert reg.idle_session_ids(0) == []


def test_activity_bump_keeps_session_alive() -> None:
    reg = PrompterLiveRegistry()
    s = reg.open("s1", "intake-1")
    s.last_activity = time.monotonic() - 4000
    assert ("s1", "intake-1") in reg.idle_session_ids(1800)
    reg.push("s1", {"kind": "text", "text": "hi"})  # an agent turn = activity
    assert reg.idle_session_ids(1800) == []  # no longer idle


def test_close_by_agent_closes_matching_sessions_with_error() -> None:
    reg = PrompterLiveRegistry()
    reg.open("s1", "intake-1")
    reg.open("s2", "secretary-1")  # different agent — must survive
    closed = reg.close_by_agent("intake-1", error="cost cap")
    assert closed == ["s1"]
    assert reg.get("s1") is None  # closed + popped
    assert reg.is_alive("s2")  # untouched
    # The error event is queued before the close sentinel so the panel sees it.
    sess = reg.open("s3", "intake-1")
    reg.close_by_agent("intake-1", error="boom")
    assert sess.queue.get_nowait() == {"kind": "error", "text": "boom"}


def test_park_and_find_by_task() -> None:
    """A parked session is discoverable by task id for board-feedback injection."""
    reg = PrompterLiveRegistry()
    session = reg.open("s1", "intake-1")
    assert reg.park("s1", "task-abc") is True
    assert session.task_id == "task-abc"
    assert reg.find_by_task("task-abc") is session
    assert reg.find_by_task("task-other") is None


def test_park_missing_session_returns_false() -> None:
    reg = PrompterLiveRegistry()
    assert reg.park("nope", "task-abc") is False


def test_find_by_task_ignores_closed_session() -> None:
    """A reaped parked session is not returned (the cold re-draft path covers it)."""
    reg = PrompterLiveRegistry()
    reg.open("s1", "intake-1")
    reg.park("s1", "task-abc")
    reg.close("s1")
    assert reg.find_by_task("task-abc") is None


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
async def test_stream_keepalive_keeps_watched_chat_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An open SSE stream refreshes last_activity even with no events, so a
    human reading a proposal (tab open, not typing) is not idle-reaped. Without
    this the chat "drops after a while" mid-review."""
    monkeypatch.setattr(pl, "_STREAM_KEEPALIVE_SECONDS", 0.01)
    reg = PrompterLiveRegistry()
    session = reg.open("s1", "intake-1")
    session.last_activity = time.monotonic() - 4000  # silent for >1h

    # Before anyone connects, the abandoned-looking chat IS idle-reapable.
    assert ("s1", "intake-1") in reg.idle_session_ids(1800)

    async def watch() -> None:
        async for _ in reg.stream("s1"):
            pass

    task = asyncio.create_task(watch())
    try:
        await asyncio.sleep(0.05)  # let a keepalive tick fire on the open stream
        # The connected stream refreshed activity → no longer idle.
        assert reg.idle_session_ids(1800) == []
    finally:
        reg.close("s1")
        await asyncio.wait_for(task, timeout=1.0)


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


def test_has_live_agent_tracks_any_session_for_that_agent() -> None:
    """Backs the "is the Secretary live under ANY session id" check — distinct
    from is_alive, which needs the caller's own session id."""
    reg = PrompterLiveRegistry()
    assert reg.has_live_agent("secretary-1") is False  # nothing open yet

    reg.open("device-a", "secretary-1")
    assert reg.has_live_agent("secretary-1") is True
    assert reg.has_live_agent("intake-1") is False  # different agent, untouched

    reg.close("device-a")
    assert reg.has_live_agent("secretary-1") is False  # closed -> gone

    # A second session id for the SAME agent still counts as live.
    reg.open("device-b", "secretary-1")
    assert reg.has_live_agent("secretary-1") is True


def test_registry_singleton() -> None:
    assert get_live_registry() is get_live_registry()
