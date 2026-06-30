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
    # A no-body failure still surfaces the status; ``detail`` is None when the
    # body is absent/unparseable (#57 — the body is now captured, not dropped).
    assert result == {"error": "http_503", "detail": None}


@pytest.mark.asyncio
async def test_post_event_captures_body_on_non_success() -> None:
    """#57: on a non-success response the relay's body carries the real reason
    (e.g. 'session not in MegaTask scope' on a 422); _post_event must capture it
    under ``detail`` so the intake agent gets actionable remediation instead of an
    opaque ``http_422`` token."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "session not in MegaTask scope"})

    async with _client(handler) as client:
        result = await intake_server.post_draft("s", {}, client=client)
    assert result["error"] == "http_422"
    assert result["detail"] == {"detail": "session not in MegaTask scope"}


@pytest.mark.asyncio
async def test_propose_batch_result_string_includes_relay_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The propose_batch failure string surfaces the captured relay body so the
    grok intake agent can tell whether to re-shape the draft or surface to the
    CEO (#57). ``post_batch`` is stubbed to the dict _post_event now returns when
    the relay rejects with a body."""

    async def _relay_rejected(
        _session_id: str, _payload: dict[str, Any], **_kw: Any
    ) -> dict[str, Any]:
        return {
            "error": "http_422",
            "detail": {"detail": "session not in MegaTask scope"},
        }

    monkeypatch.setattr(intake_server, "post_batch", _relay_rejected)
    monkeypatch.setenv("ROBOCO_PROMPTER_SESSION_ID", "sess-1")
    msg = await intake_server.propose_batch([{"title": "A"}], "MegaTask")
    assert "Could not submit the MegaTask to the panel" in msg
    assert "session not in MegaTask scope" in msg


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


# ---------------------------------------------------------------------------
# #163: propose_batch must accept ``name`` as well as ``title`` (intake drafts
# in the wild have used both) and report the drop reason instead of silently
# vanishing drafts.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_batch_accepts_name_as_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draft using ``name`` instead of ``title`` is well-formed (#163)."""
    monkeypatch.setenv("ROBOCO_PROMPTER_SESSION_ID", "sess-1")
    captured: dict[str, Any] = {}

    async def _spy(_sid: str, batch: dict[str, Any]) -> dict[str, Any]:
        captured["batch"] = batch
        return {"ok": True}

    monkeypatch.setattr(intake_server, "post_batch", _spy)
    msg = await intake_server.propose_batch([{"name": "Build X"}], "MegaTask")

    assert "MegaTask submitted" in msg
    assert "dropped" not in msg  # none dropped → no drop note
    data = captured["batch"]
    assert data["dropped"] == 0
    assert len(data["drafts"]) == 1
    # ``name`` normalized to ``title`` for the relay.
    assert data["drafts"][0]["title"] == "Build X"
    assert data["drafts"][0]["name"] == "Build X"


@pytest.mark.asyncio
async def test_propose_batch_reports_dropped_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mixed batch posts the well-formed drafts and reports how many dropped."""
    monkeypatch.setenv("ROBOCO_PROMPTER_SESSION_ID", "sess-1")
    captured: dict[str, Any] = {}

    async def _spy(_sid: str, batch: dict[str, Any]) -> dict[str, Any]:
        captured["batch"] = batch
        return {"ok": True}

    monkeypatch.setattr(intake_server, "post_batch", _spy)
    msg = await intake_server.propose_batch(
        [{"title": "A"}, {"no_title": True}, {"name": "B"}], "MegaTask"
    )

    assert "MegaTask submitted" in msg
    assert "1 draft" in msg  # exactly the one malformed entry dropped
    data = captured["batch"]
    assert data["dropped"] == 1
    assert [d["title"] for d in data["drafts"]] == ["A", "B"]


@pytest.mark.asyncio
async def test_propose_batch_malformed_message_mentions_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every draft is malformed, the hint names ``name`` as an alternative."""
    monkeypatch.setenv("ROBOCO_PROMPTER_SESSION_ID", "sess-1")
    posted = False

    async def _spy(_sid: str, _batch: dict[str, Any]) -> dict[str, Any]:
        nonlocal posted
        posted = True
        return {"ok": True}

    monkeypatch.setattr(intake_server, "post_batch", _spy)
    msg = await intake_server.propose_batch([{"no_title": True}], "MegaTask")
    assert "no well-formed task drafts" in msg
    assert "name" in msg
    assert posted is False


@pytest.mark.asyncio
async def test_propose_batch_does_not_mutate_caller_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``name``-only draft is normalized onto a copy, not the caller's dict."""
    monkeypatch.setenv("ROBOCO_PROMPTER_SESSION_ID", "sess-1")

    async def _spy(_sid: str, _batch: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(intake_server, "post_batch", _spy)
    draft = {"name": "Build X"}
    await intake_server.propose_batch([draft], "MegaTask")
    # Caller's dict is unchanged — no synthesized ``title`` key added in place.
    assert "title" not in draft


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
