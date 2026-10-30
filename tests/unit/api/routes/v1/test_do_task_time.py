"""Unit tests for POST /api/v1/do/task_time.

Same minimal-router harness as test_do.py: ContentActions is mocked, no DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roboco.api.deps import get_content_actions
from roboco.api.routes.v1.do import router
from roboco.services.gateway.content_actions import ContentActions

_HTTP_200 = 200
_HTTP_422 = 422
_HTTP_OK = 200

_AGENT_ID = str(uuid4())
_TASK_ID = str(uuid4())
_HEADERS = {"X-Agent-ID": _AGENT_ID}


def _make_envelope(
    status: str = "measured",
    task_id: str | None = None,
    extra: dict | None = None,
) -> MagicMock:
    env = MagicMock()
    payload: dict = {"status": status, "task_id": task_id, "next": "continue"}
    if extra:
        payload.update(extra)
    env.as_dict.return_value = payload
    return env


def _build_app(mock_actions: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_content_actions] = lambda: mock_actions
    return app


@pytest.mark.asyncio
async def test_task_time_returns_measured_envelope() -> None:
    """POST /api/v1/do/task_time with a valid task_id returns 200 with evidence."""
    evidence = {
        "task_id": _TASK_ID,
        "wall_seconds": {"age": 3600.0},
        "active_seconds": {"age": 1800.0},
        "downtime_windows": [],
    }
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.task_time = AsyncMock(
        return_value=_make_envelope(
            status="measured", task_id=_TASK_ID, extra={"evidence": evidence}
        )
    )
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v1/do/task_time",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "measured"
    assert (
        body["evidence"]["active_seconds"]["age"]
        < body["evidence"]["wall_seconds"]["age"]
    )
    mock_actions.task_time.assert_awaited_once()
    assert str(mock_actions.task_time.call_args.kwargs["task_id"]) == _TASK_ID


@pytest.mark.asyncio
async def test_task_time_accepts_non_uuid_ref_for_downstream_resolution() -> None:
    """A non-UUID task_id is no longer a 422: short refs (8+ hex chars) and
    even malformed refs travel to ContentActions, whose resolver answers
    with an incomplete_input envelope (2026-09-28: the strict UUID schema
    422'd the short ids the task cards display and wedged be-qa's review)."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.task_time = AsyncMock(return_value=_make_envelope())
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v1/do/task_time",
        json={"task_id": "372eac39"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_OK
    mock_actions.task_time.assert_awaited_once()
    assert mock_actions.task_time.call_args.kwargs["task_id"] == "372eac39"


@pytest.mark.asyncio
async def test_task_time_missing_task_id_returns_422() -> None:
    """A missing task_id still fails schema validation (the field is
    required) before ContentActions is called."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.task_time = AsyncMock(return_value=_make_envelope())
    client = TestClient(_build_app(mock_actions))

    resp = client.post("/api/v1/do/task_time", json={}, headers=_HEADERS)

    assert resp.status_code == _HTTP_422
    mock_actions.task_time.assert_not_awaited()
