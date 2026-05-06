"""Unit tests for /api/v2/do/* endpoints.

Uses a minimal FastAPI test client built from the do router only.
No DB required — ContentActions is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roboco.api.deps import get_content_actions
from roboco.api.routes.v2.do import router
from roboco.services.gateway.content_actions import ContentActions

_HTTP_200 = 200
_HTTP_422 = 422

_AGENT_ID = str(uuid4())
_TASK_ID = str(uuid4())
_HEADERS = {"X-Agent-ID": _AGENT_ID}


def _make_envelope(
    status: str = "ok",
    task_id: str | None = None,
    extra: dict | None = None,
) -> MagicMock:
    """Return a mock Envelope whose as_dict() returns a predictable payload."""
    env = MagicMock()
    payload: dict = {"status": status, "task_id": task_id, "next": "continue"}
    if extra:
        payload.update(extra)
    env.as_dict.return_value = payload
    return env


def _build_app(mock_actions: MagicMock) -> FastAPI:
    """Build minimal FastAPI app with the do router and a mocked dep."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_content_actions] = lambda: mock_actions
    return app


@pytest.mark.asyncio
async def test_commit_descriptive_message_returns_ok() -> None:
    """POST /api/v2/do/commit with a descriptive message returns 200 ok."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.commit = AsyncMock(
        return_value=_make_envelope(status="ok", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/commit",
        json={"message": "add user authentication endpoint"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "ok"
    mock_actions.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_commit_wip_message_returns_invalid_state() -> None:
    """POST /api/v2/do/commit with 'wip' returns invalid_state envelope."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.commit = AsyncMock(return_value=_make_envelope(status="invalid_state"))
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/commit",
        json={"message": "wip"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "invalid_state"
    mock_actions.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_reflect_scope_returns_ok() -> None:
    """POST /api/v2/do/note with scope='reflect' returns 200 ok."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.note = AsyncMock(return_value=_make_envelope(status="noted"))
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/note",
        json={"text": "learned how HyDE works", "scope": "reflect"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "noted"
    mock_actions.note.assert_awaited_once()
    assert mock_actions.note.call_args.kwargs["scope"] == "reflect"


@pytest.mark.asyncio
async def test_note_garbage_scope_returns_invalid_state() -> None:
    """POST /api/v2/do/note with scope='garbage' returns invalid_state envelope."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.note = AsyncMock(return_value=_make_envelope(status="invalid_state"))
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/note",
        json={"text": "some note", "scope": "garbage"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "invalid_state"
    mock_actions.note.assert_awaited_once()


@pytest.mark.asyncio
async def test_say_with_explicit_task_id_returns_ok() -> None:
    """POST /api/v2/do/say with task_id explicit returns 200 ok."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.say = AsyncMock(
        return_value=_make_envelope(status="posted", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/say",
        json={"channel": "backend-cell", "text": "PR is ready", "task_id": _TASK_ID},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "posted"
    mock_actions.say.assert_awaited_once()
    assert str(mock_actions.say.call_args.kwargs["task_id"]) == _TASK_ID


@pytest.mark.asyncio
async def test_say_without_task_id_auto_injects() -> None:
    """POST /api/v2/do/say with task_id null passes None; ContentActions injects."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.say = AsyncMock(
        return_value=_make_envelope(status="posted", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/say",
        json={"channel": "backend-cell", "text": "stand-up update"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    # task_id was None in the request body — handler passes it through as None
    call_kwargs = mock_actions.say.call_args.kwargs
    assert call_kwargs["task_id"] is None
    mock_actions.say.assert_awaited_once()


@pytest.mark.asyncio
async def test_dm_with_no_task_context_returns_invalid_state() -> None:
    """POST /api/v2/do/dm with no task context returns invalid_state envelope."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.dm = AsyncMock(return_value=_make_envelope(status="invalid_state"))
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/dm",
        json={"recipient": "be-qa-1", "text": "please review"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "invalid_state"
    mock_actions.dm.assert_awaited_once()


@pytest.mark.asyncio
async def test_evidence_with_task_id_returns_evidence_envelope() -> None:
    """POST /api/v2/do/evidence with task_id returns 200 with evidence in response."""
    evidence_payload = {"commits": ["abc123"], "diff_summary": "added 3 files"}
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.evidence = AsyncMock(
        return_value=_make_envelope(
            status="in_progress", task_id=_TASK_ID, extra={"evidence": evidence_payload}
        )
    )
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/evidence",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "in_progress"
    assert "evidence" in body
    assert body["evidence"]["commits"] == ["abc123"]
    mock_actions.evidence.assert_awaited_once()
    assert str(mock_actions.evidence.call_args.kwargs["task_id"]) == _TASK_ID


@pytest.mark.asyncio
async def test_notify_dispatches_target_text_priority() -> None:
    """POST /api/v2/do/notify forwards target/text/priority to ContentActions."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.notify = AsyncMock(
        return_value=_make_envelope(status="ok", task_id=None)
    )
    client = TestClient(_build_app(mock_actions))
    resp = client.post(
        "/api/v2/do/notify",
        json={"target": "be-pm", "text": "ack me", "priority": "normal"},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_actions.notify.assert_awaited_once()
    call_kwargs = mock_actions.notify.call_args.kwargs
    assert call_kwargs["target"] == "be-pm"
    assert call_kwargs["text"] == "ack me"
