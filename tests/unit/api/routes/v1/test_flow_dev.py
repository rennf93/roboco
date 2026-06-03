"""Unit tests for /api/v1/flow/developer/* endpoints.

Uses a minimal FastAPI test client built from the new router only.
No DB required — Choreographer is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roboco.api.deps import get_choreographer
from roboco.api.routes.v1.flow_dev import router

_HTTP_200 = 200
_HTTP_422 = 422

_AGENT_ID = str(uuid4())
_TASK_ID = str(uuid4())
_HEADERS = {"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "developer"}


def _make_envelope(status: str = "ok", task_id: str | None = None) -> MagicMock:
    """Return a mock Envelope whose as_dict() returns a predictable payload."""
    env = MagicMock()
    env.as_dict.return_value = {"status": status, "task_id": task_id, "next": "..."}
    return env


def _build_app(mock_choreographer: MagicMock) -> FastAPI:
    """Build minimal FastAPI app with the flow_dev router and a mocked dep."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_choreographer] = lambda: mock_choreographer
    return app


@pytest.mark.asyncio
async def test_give_me_work_returns_envelope() -> None:
    """POST /api/v1/flow/developer/give_me_work returns 200 with envelope shape."""
    mock_chore = MagicMock()
    mock_chore.give_me_work = AsyncMock(return_value=_make_envelope(status="idle"))
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/developer/give_me_work",
        json={},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "idle"
    mock_chore.give_me_work.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_will_work_on_dispatches_task_id() -> None:
    """POST /api/v1/flow/developer/i_will_work_on forwards task_id and plan."""
    mock_chore = MagicMock()
    mock_chore.i_will_work_on = AsyncMock(
        return_value=_make_envelope(status="in_progress", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/developer/i_will_work_on",
        json={"task_id": _TASK_ID, "plan": "implement the feature"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "in_progress"
    mock_chore.i_will_work_on.assert_awaited_once()
    call_args = mock_chore.i_will_work_on.call_args
    # second positional arg is task_id (UUID), third is plan
    assert str(call_args.args[1]) == _TASK_ID
    assert call_args.args[2] == "implement the feature"


@pytest.mark.asyncio
async def test_i_am_done_dispatches_task_and_notes() -> None:
    """POST /api/v1/flow/developer/i_am_done forwards task_id and notes."""
    mock_chore = MagicMock()
    mock_chore.i_am_done = AsyncMock(
        return_value=_make_envelope(status="awaiting_qa", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/developer/i_am_done",
        json={"task_id": _TASK_ID, "notes": "all tests pass"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "awaiting_qa"
    mock_chore.i_am_done.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_blocked_dispatches_reason() -> None:
    """POST /api/v1/flow/developer/i_am_blocked forwards task_id and reason."""
    mock_chore = MagicMock()
    mock_chore.i_am_blocked = AsyncMock(
        return_value=_make_envelope(status="blocked", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/developer/i_am_blocked",
        json={"task_id": _TASK_ID, "reason": "waiting for design spec"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.i_am_blocked.assert_awaited_once()
    assert mock_chore.i_am_blocked.call_args.args[2] == "waiting for design spec"


@pytest.mark.asyncio
async def test_i_am_idle_dispatches_agent_id() -> None:
    """POST /api/v1/flow/developer/i_am_idle delegates to Choreographer.i_am_idle."""
    mock_chore = MagicMock()
    mock_chore.i_am_idle = AsyncMock(return_value=_make_envelope(status="idle"))
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/developer/i_am_idle",
        json={},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "idle"
    mock_chore.i_am_idle.assert_awaited_once()


def test_i_am_blocked_rejects_empty_reason() -> None:
    """POST i_am_blocked rejects empty reason (min_length=1)."""
    mock_chore = MagicMock()
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/developer/i_am_blocked",
        json={"task_id": _TASK_ID, "reason": ""},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_422


@pytest.mark.asyncio
async def test_open_pr_dispatches_task_id() -> None:
    """POST open_pr forwards task_id."""
    mock_chore = MagicMock()
    mock_chore.open_pr = AsyncMock(
        return_value=_make_envelope(status="awaiting_qa", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v1/flow/developer/open_pr",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_chore.open_pr.assert_awaited_once()


@pytest.mark.asyncio
async def test_unclaim_dispatches_task_id() -> None:
    """POST unclaim forwards task_id."""
    mock_chore = MagicMock()
    mock_chore.unclaim = AsyncMock(
        return_value=_make_envelope(status="pending", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v1/flow/developer/unclaim",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_chore.unclaim.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_dispatches_task_id() -> None:
    """POST resume forwards task_id."""
    mock_chore = MagicMock()
    mock_chore.resume = AsyncMock(
        return_value=_make_envelope(status="in_progress", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v1/flow/developer/resume",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_chore.resume.assert_awaited_once()
