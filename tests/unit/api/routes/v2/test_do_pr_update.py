"""Unit tests for POST /api/v2/do/pr_update — route + schema.

Pydantic's model_validator must reject an all-None payload with 422
before ContentActions ever runs; a valid payload must forward title /
body / reviewers verbatim.
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


def _make_envelope(payload: dict | None = None) -> MagicMock:
    env = MagicMock()
    base = {"status": "in_progress", "task_id": _TASK_ID, "next": "continue"}
    if payload:
        base.update(payload)
    env.as_dict.return_value = base
    return env


def _build_app(mock_actions: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_content_actions] = lambda: mock_actions
    return app


@pytest.mark.asyncio
async def test_pr_update_all_none_returns_422() -> None:
    """Body with task_id only (no title/body/reviewers) → 422 from validator."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.pr_update = AsyncMock(return_value=_make_envelope())
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/pr_update",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_422
    mock_actions.pr_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_update_title_only_forwards_to_content_actions() -> None:
    """Body with title only → 200, forwarded to ContentActions.pr_update."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.pr_update = AsyncMock(
        return_value=_make_envelope({"evidence": {"updated_fields": ["title"]}})
    )
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/pr_update",
        json={"task_id": _TASK_ID, "title": "new title"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_actions.pr_update.assert_awaited_once()
    call_kwargs = mock_actions.pr_update.call_args.kwargs
    assert str(call_kwargs["task_id"]) == _TASK_ID
    assert call_kwargs["title"] == "new title"
    assert call_kwargs["body"] is None
    assert call_kwargs["reviewers"] is None


@pytest.mark.asyncio
async def test_pr_update_all_fields_forwarded() -> None:
    """Body with title + body + reviewers → all three forwarded verbatim."""
    mock_actions = MagicMock(spec=ContentActions)
    mock_actions.pr_update = AsyncMock(
        return_value=_make_envelope(
            {"evidence": {"updated_fields": ["title", "body", "reviewers"]}}
        )
    )
    client = TestClient(_build_app(mock_actions))

    resp = client.post(
        "/api/v2/do/pr_update",
        json={
            "task_id": _TASK_ID,
            "title": "t",
            "body": "b",
            "reviewers": ["be-dev-2", "be-qa"],
        },
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    call_kwargs = mock_actions.pr_update.call_args.kwargs
    assert call_kwargs["title"] == "t"
    assert call_kwargs["body"] == "b"
    assert call_kwargs["reviewers"] == ["be-dev-2", "be-qa"]
