"""roboco-intake MCP server — propose_draft delivers the draft to the relay."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from roboco.mcp import intake_server


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_post_draft_posts_to_the_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_API_URL", "http://orch:8000")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as client:
        result = await intake_server.post_draft(
            "sess-1", {"title": "Build X"}, client=client
        )

    assert result == {"ok": True}
    assert seen["url"] == "http://orch:8000/api/prompter/live/sess-1/events"
    assert seen["json"]["kind"] == "draft"
    assert seen["json"]["tool"] == "propose_draft"
    assert seen["json"]["data"] == {"title": "Build X"}


@pytest.mark.asyncio
async def test_post_draft_forwards_batch_collision_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch draft carries its collision surface through to the relay intact."""
    monkeypatch.setenv("ROBOCO_API_URL", "http://orch:8000")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"ok": True})

    draft = {
        "title": "Fix charts",
        "intends_to_touch": ["svc/dashboard.py", "page/metrics.tsx"],
        "adds_migration": True,
        "touches_shared": False,
    }
    async with _client(handler) as client:
        result = await intake_server.post_draft("sess-1", draft, client=client)

    assert result == {"ok": True}
    data = seen["json"]["data"]
    assert data["intends_to_touch"] == ["svc/dashboard.py", "page/metrics.tsx"]
    assert data["adds_migration"] is True
    assert data["touches_shared"] is False


@pytest.mark.asyncio
async def test_post_draft_reports_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with _client(handler) as client:
        result = await intake_server.post_draft("s", {}, client=client)
    assert result == {"error": "http_503"}


@pytest.mark.asyncio
async def test_post_draft_reports_request_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(handler) as client:
        result = await intake_server.post_draft("s", {}, client=client)
    assert result["error"] == "request_failed"
    assert "boom" in result["detail"]


@pytest.mark.asyncio
async def test_post_batch_posts_a_batch_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_API_URL", "http://orch:8000")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"ok": True})

    batch = {"drafts": [{"title": "A"}, {"title": "B"}], "title": "MegaTask"}
    async with _client(handler) as client:
        result = await intake_server.post_batch("sess-1", batch, client=client)

    assert result == {"ok": True}
    assert seen["url"] == "http://orch:8000/api/prompter/live/sess-1/events"
    assert seen["json"]["kind"] == "batch"
    assert seen["json"]["tool"] == "propose_batch"
    assert [d["title"] for d in seen["json"]["data"]["drafts"]] == ["A", "B"]


@pytest.mark.asyncio
async def test_propose_batch_acks_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_PROMPTER_SESSION_ID", "sess-1")

    async def _ok(_sid: str, _batch: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(intake_server, "post_batch", _ok)
    msg = await intake_server.propose_batch([{"title": "A"}], "MegaTask")
    assert "MegaTask submitted" in msg


@pytest.mark.asyncio
async def test_propose_batch_requires_a_live_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROBOCO_PROMPTER_SESSION_ID", raising=False)
    msg = await intake_server.propose_batch([{"title": "A"}], "MegaTask")
    assert "No live session id" in msg


@pytest.mark.asyncio
async def test_propose_batch_refuses_empty_without_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_PROMPTER_SESSION_ID", "sess-1")
    posted = False

    async def _spy(_sid: str, _batch: dict[str, Any]) -> dict[str, Any]:
        nonlocal posted
        posted = True
        return {"ok": True}

    monkeypatch.setattr(intake_server, "post_batch", _spy)
    # No titles anywhere → nothing well-formed → don't POST, tell the agent.
    msg = await intake_server.propose_batch([{"no_title": True}], "MegaTask")
    assert "no well-formed task drafts" in msg
    assert posted is False


@pytest.mark.asyncio
async def test_propose_draft_requires_a_live_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROBOCO_PROMPTER_SESSION_ID", raising=False)
    msg = await intake_server.propose_draft({"title": "X"})
    assert "No live session id" in msg


@pytest.mark.asyncio
async def test_propose_draft_acks_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_PROMPTER_SESSION_ID", "sess-1")

    async def _ok(_sid: str, _draft: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(intake_server, "post_draft", _ok)
    msg = await intake_server.propose_draft({"title": "X"})
    assert "Draft submitted" in msg


@pytest.mark.asyncio
async def test_propose_draft_reports_relay_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_PROMPTER_SESSION_ID", "sess-1")

    async def _fail(_sid: str, _draft: dict[str, Any]) -> dict[str, Any]:
        return {"error": "http_503"}

    monkeypatch.setattr(intake_server, "post_draft", _fail)
    msg = await intake_server.propose_draft({"title": "X"})
    assert "Could not submit the draft" in msg
    assert "http_503" in msg
