"""Unit tests for /api/v2/flow/documenter/* endpoints.

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
from roboco.api.routes.v2.flow_doc import router

_HTTP_200 = 200
_HTTP_422 = 422

_AGENT_ID = str(uuid4())
_TASK_ID = str(uuid4())
_HEADERS = {"X-Agent-ID": _AGENT_ID}


def _make_envelope(
    status: str = "ok", task_id: str | None = None, **extra: object
) -> MagicMock:
    """Return a mock Envelope whose as_dict() returns a predictable payload."""
    env = MagicMock()
    payload: dict[str, object] = {"status": status, "task_id": task_id, "next": "..."}
    payload.update(extra)
    env.as_dict.return_value = payload
    return env


def _build_app(mock_choreographer: MagicMock) -> FastAPI:
    """Build minimal FastAPI app with the flow_doc router and a mocked dep."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_choreographer] = lambda: mock_choreographer
    return app


@pytest.mark.asyncio
async def test_give_me_work_returns_envelope() -> None:
    """POST /api/v2/flow/documenter/give_me_work returns 200 with envelope shape."""
    mock_chore = MagicMock()
    mock_chore.give_me_work = AsyncMock(return_value=_make_envelope(status="idle"))
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/documenter/give_me_work",
        json={},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "idle"
    mock_chore.give_me_work.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_doc_task_dispatches_task_id() -> None:
    """POST /api/v2/flow/documenter/claim_doc_task forwards task_id."""
    mock_chore = MagicMock()
    mock_chore.claim_doc_task = AsyncMock(
        return_value=_make_envelope(status="awaiting_documentation", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/documenter/claim_doc_task",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "awaiting_documentation"
    mock_chore.claim_doc_task.assert_awaited_once()
    call_args = mock_chore.claim_doc_task.call_args
    assert str(call_args.args[1]) == _TASK_ID


@pytest.mark.asyncio
async def test_i_documented_dispatches_notes_and_files() -> None:
    """POST /api/v2/flow/documenter/i_documented forwards notes and files."""
    mock_chore = MagicMock()
    mock_chore.i_documented = AsyncMock(
        return_value=_make_envelope(status="awaiting_pm_review", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/documenter/i_documented",
        json={
            "task_id": _TASK_ID,
            "notes": "Documented the auth endpoint in docs/api/auth.md",
            "files": ["docs/api/auth.md"],
        },
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "awaiting_pm_review"
    mock_chore.i_documented.assert_awaited_once()
    call_args = mock_chore.i_documented.call_args
    assert str(call_args.args[1]) == _TASK_ID
    assert call_args.args[2] == "Documented the auth endpoint in docs/api/auth.md"
    assert call_args.args[3] == ["docs/api/auth.md"]


@pytest.mark.asyncio
async def test_i_am_idle_dispatches_agent_id() -> None:
    """POST /api/v2/flow/documenter/i_am_idle delegates to Choreographer.i_am_idle."""
    mock_chore = MagicMock()
    mock_chore.i_am_idle = AsyncMock(return_value=_make_envelope(status="idle"))
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/documenter/i_am_idle",
        json={},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "idle"
    mock_chore.i_am_idle.assert_awaited_once()


def test_i_documented_rejects_empty_notes() -> None:
    """POST i_documented rejects empty notes (min_length=1)."""
    mock_chore = MagicMock()
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/documenter/i_documented",
        json={"task_id": _TASK_ID, "notes": "", "files": ["docs/readme.md"]},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_422


def test_i_documented_rejects_empty_files_list() -> None:
    """POST i_documented rejects empty files list (min_length=1)."""
    mock_chore = MagicMock()
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/documenter/i_documented",
        json={"task_id": _TASK_ID, "notes": "some docs", "files": []},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_422
