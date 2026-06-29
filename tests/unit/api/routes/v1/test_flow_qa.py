"""Unit tests for /api/v1/flow/qa/* endpoints.

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
from roboco.api.routes.v1.flow_qa import router

_HTTP_200 = 200
_HTTP_422 = 422

_AGENT_ID = str(uuid4())
_TASK_ID = str(uuid4())
_HEADERS = {"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "qa"}


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
    """Build minimal FastAPI app with the flow_qa router and a mocked dep."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_choreographer] = lambda: mock_choreographer
    return app


@pytest.mark.asyncio
async def test_give_me_work_returns_envelope() -> None:
    """POST /api/v1/flow/qa/give_me_work returns 200 with envelope shape."""
    mock_chore = MagicMock()
    mock_chore.give_me_work = AsyncMock(return_value=_make_envelope(status="idle"))
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/qa/give_me_work",
        json={},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "idle"
    mock_chore.give_me_work.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_review_dispatches_task_id() -> None:
    """POST /api/v1/flow/qa/claim_review returns 200 with evidence.pr_url in body."""
    mock_chore = MagicMock()
    mock_chore.claim_review = AsyncMock(
        return_value=_make_envelope(
            status="claimed",
            task_id=_TASK_ID,
            evidence={"pr_url": "https://github.com/org/repo/pull/42"},
        )
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/qa/claim_review",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["evidence"]["pr_url"] == "https://github.com/org/repo/pull/42"
    mock_chore.claim_review.assert_awaited_once()
    call_args = mock_chore.claim_review.call_args
    assert str(call_args.args[1]) == _TASK_ID


@pytest.mark.asyncio
async def test_pass_review_with_notes_returns_awaiting_documentation() -> None:
    """POST /api/v1/flow/qa/pass with notes returns 200 with awaiting_documentation."""
    mock_chore = MagicMock()
    mock_chore.pass_review = AsyncMock(
        return_value=_make_envelope(status="awaiting_documentation", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/qa/pass",
        json={
            "task_id": _TASK_ID,
            "notes": "All acceptance criteria met, tests green.",
        },
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "awaiting_documentation"
    mock_chore.pass_review.assert_awaited_once()
    call_args = mock_chore.pass_review.call_args
    assert call_args.args[2] == "All acceptance criteria met, tests green."


@pytest.mark.asyncio
async def test_pass_review_short_notes_returns_tracing_gap_envelope() -> None:
    """POST /api/v1/flow/qa/pass with minimal notes relies on choreographer to gate."""
    mock_chore = MagicMock()
    mock_chore.pass_review = AsyncMock(
        return_value=_make_envelope(status="tracing_gap", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/qa/pass",
        json={"task_id": _TASK_ID, "notes": "ok"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "tracing_gap"
    mock_chore.pass_review.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_review_with_issues_returns_needs_revision() -> None:
    """POST /api/v1/flow/qa/fail with issues returns 200 with needs_revision status."""
    mock_chore = MagicMock()
    mock_chore.fail_review = AsyncMock(
        return_value=_make_envelope(status="needs_revision", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/qa/fail",
        json={
            "task_id": _TASK_ID,
            "issues": ["Missing error handling", "No unit tests"],
        },
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "needs_revision"
    mock_chore.fail_review.assert_awaited_once()
    call_args = mock_chore.fail_review.call_args
    assert call_args.args[2] == ["Missing error handling", "No unit tests"]


def test_fail_review_rejects_empty_issues_list() -> None:
    """POST /api/v1/flow/qa/fail with empty issues list is rejected with 422."""
    mock_chore = MagicMock()
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/qa/fail",
        json={"task_id": _TASK_ID, "issues": []},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_422


@pytest.mark.asyncio
async def test_unclaim_dispatches_task_id() -> None:
    mock_chore = MagicMock()
    mock_chore.unclaim = AsyncMock(
        return_value=_make_envelope(status="awaiting_qa", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v1/flow/qa/unclaim",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_chore.unclaim.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_dispatches_task_id() -> None:
    mock_chore = MagicMock()
    mock_chore.resume = AsyncMock(
        return_value=_make_envelope(status="claimed", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v1/flow/qa/resume",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_chore.resume.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_idle_dispatches_agent_id() -> None:
    mock_chore = MagicMock()
    mock_chore.i_am_idle = AsyncMock(return_value=_make_envelope(status="idle"))
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v1/flow/qa/i_am_idle",
        json={},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_chore.i_am_idle.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_blocked_dispatches_to_choreographer() -> None:
    """POST /api/v1/flow/qa/i_am_blocked returns an envelope (the QA manifest
    registers i_am_blocked) rather than a raw 404."""
    mock_chore = MagicMock()
    mock_chore.i_am_blocked = AsyncMock(
        return_value=_make_envelope(status="blocked", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v1/flow/qa/i_am_blocked",
        json={"task_id": _TASK_ID, "reason": "PR diff won't load"},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "blocked"
    mock_chore.i_am_blocked.assert_awaited_once()
