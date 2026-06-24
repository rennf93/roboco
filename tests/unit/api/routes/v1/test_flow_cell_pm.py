"""Unit tests for /api/v1/flow/cell_pm/* endpoints.

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
from roboco.api.routes.v1.flow_cell_pm import router

_HTTP_200 = 200
_HTTP_422 = 422

_AGENT_ID = str(uuid4())
_TASK_ID = str(uuid4())
_HEADERS = {"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "cell_pm"}


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
    """Build minimal FastAPI app with the flow_cell_pm router and a mocked dep."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_choreographer] = lambda: mock_choreographer
    return app


@pytest.mark.asyncio
async def test_give_me_work_returns_envelope() -> None:
    """POST /api/v1/flow/cell_pm/give_me_work returns 200 with envelope shape.

    Cell PM's give_me_work routes to ``pm_give_me_work`` so the response
    surfaces non-pending PM tasks (paused, awaiting_pm_review) too.
    """
    mock_chore = MagicMock()
    mock_chore.pm_give_me_work = AsyncMock(return_value=_make_envelope(status="idle"))
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/give_me_work",
        json={},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "idle"
    mock_chore.pm_give_me_work.assert_awaited_once()


@pytest.mark.asyncio
async def test_triage_returns_envelope() -> None:
    """POST /api/v1/flow/cell_pm/triage returns 200 with task or idle status."""
    mock_chore = MagicMock()
    mock_chore.triage = AsyncMock(
        return_value=_make_envelope(status="awaiting_pm_review", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/triage",
        json={},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "awaiting_pm_review"
    mock_chore.triage.assert_awaited_once()


@pytest.mark.asyncio
async def test_unblock_dispatches_task_id_with_restore_true() -> None:
    """POST /api/v1/flow/cell_pm/unblock forwards task_id and restore=True."""
    mock_chore = MagicMock()
    mock_chore.unblock = AsyncMock(
        return_value=_make_envelope(status="in_progress", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/unblock",
        json={"task_id": _TASK_ID, "reason": "block resolved upstream; restoring"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "in_progress"
    mock_chore.unblock.assert_awaited_once()
    call_kwargs = mock_chore.unblock.call_args.kwargs
    assert call_kwargs["restore"] is True
    assert mock_chore.unblock.call_args.args[2] == "block resolved upstream; restoring"


@pytest.mark.asyncio
async def test_unblock_with_restore_false() -> None:
    """POST /api/v1/flow/cell_pm/unblock forwards restore=False when specified."""
    mock_chore = MagicMock()
    mock_chore.unblock = AsyncMock(
        return_value=_make_envelope(status="in_progress", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/unblock",
        json={
            "task_id": _TASK_ID,
            "reason": "block resolved upstream; restoring",
            "restore": False,
        },
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.unblock.assert_awaited_once()
    call_kwargs = mock_chore.unblock.call_args.kwargs
    assert call_kwargs["restore"] is False


@pytest.mark.asyncio
async def test_complete_dispatches_task_and_notes() -> None:
    """POST /api/v1/flow/cell_pm/complete forwards task_id and notes."""
    mock_chore = MagicMock()
    mock_chore.complete = AsyncMock(
        return_value=_make_envelope(status="completed", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/complete",
        json={"task_id": _TASK_ID, "notes": "All subtasks done, PR merged."},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "completed"
    mock_chore.complete.assert_awaited_once()
    call_args = mock_chore.complete.call_args
    assert str(call_args.args[1]) == _TASK_ID
    assert call_args.args[2] == "All subtasks done, PR merged."


@pytest.mark.asyncio
async def test_escalate_up_dispatches_reason() -> None:
    """POST /api/v1/flow/cell_pm/escalate_up forwards task_id and reason."""
    mock_chore = MagicMock()
    mock_chore.escalate_up = AsyncMock(
        return_value=_make_envelope(status="awaiting_pm_review", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/escalate_up",
        json={"task_id": _TASK_ID, "reason": "Cross-cell dependency needs Main PM."},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.escalate_up.assert_awaited_once()
    call_args = mock_chore.escalate_up.call_args
    assert call_args.args[2] == "Cross-cell dependency needs Main PM."


@pytest.mark.asyncio
async def test_i_am_idle_dispatches_agent_id() -> None:
    """POST /api/v1/flow/cell_pm/i_am_idle delegates to Choreographer.i_am_idle."""
    mock_chore = MagicMock()
    mock_chore.i_am_idle = AsyncMock(return_value=_make_envelope(status="idle"))
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/i_am_idle",
        json={},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "idle"
    mock_chore.i_am_idle.assert_awaited_once()


def test_complete_rejects_empty_notes() -> None:
    """POST complete rejects empty notes (min_length=1)."""
    mock_chore = MagicMock()
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/complete",
        json={"task_id": _TASK_ID, "notes": ""},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_422


def test_escalate_up_rejects_empty_reason() -> None:
    """POST escalate_up rejects empty reason (min_length=1)."""
    mock_chore = MagicMock()
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/escalate_up",
        json={"task_id": _TASK_ID, "reason": ""},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_422


@pytest.mark.asyncio
async def test_i_will_plan_dispatches_to_choreographer() -> None:
    """POST /api/v1/flow/cell_pm/i_will_plan forwards task_id and plan."""
    mock_chore = MagicMock()
    mock_chore.i_will_plan = AsyncMock(
        return_value=_make_envelope(status="in_progress", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/i_will_plan",
        json={
            "task_id": _TASK_ID,
            "plan": "break into 3 subtasks for backend",
            "approach": (
                "Decompose into a backend API slice that be-dev-1 owns end "
                "to end; QA reviews after the PR opens, documentation follows, "
                "then be-pm completes and submits up. Single-cell — no "
                "frontend or ux work; strict sequencing with no cross-cell "
                "dependencies for this planning task."
            ),
            "sub_tasks": [
                {
                    "title": "Backend API slice",
                    "description": (
                        "be-dev-1 implements the endpoint with tests, commits "
                        "with the task-id prefix, opens the leaf PR for QA."
                    ),
                }
            ],
        },
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.i_will_plan.assert_awaited_once()


@pytest.mark.asyncio
async def test_delegate_dispatches_inputs_bundle() -> None:
    """POST /api/v1/flow/cell_pm/delegate forwards body via DelegateInputs."""
    mock_chore = MagicMock()
    mock_chore.delegate = AsyncMock(
        return_value=_make_envelope(status="created", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/delegate",
        json={
            "parent_task_id": _TASK_ID,
            "title": "Implement /v1/foo",
            "description": "Add the foo endpoint with passing tests.",
            "assigned_to": "be-dev-1",
            "team": "backend",
            "task_type": "code",
            "nature": "technical",
            "estimated_complexity": "medium",
            "acceptance_criteria": ["GET /v1/foo returns 200 with body"],
        },
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.delegate.assert_awaited_once()
    inputs = mock_chore.delegate.await_args.args[2]
    assert inputs.task_type == "code"


@pytest.mark.asyncio
async def test_submit_up_dispatches_notes() -> None:
    """POST /api/v1/flow/cell_pm/submit_up forwards task_id and notes."""
    mock_chore = MagicMock()
    mock_chore.submit_up = AsyncMock(
        return_value=_make_envelope(status="awaiting_pm_review", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/submit_up",
        json={
            "task_id": _TASK_ID,
            "notes": "cell finished all subtasks, ready for main pm review",
        },
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.submit_up.assert_awaited_once()


def test_submit_up_rejects_empty_notes() -> None:
    """POST /api/v1/flow/cell_pm/submit_up rejects empty notes."""
    mock_chore = MagicMock()
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v1/flow/cell_pm/submit_up",
        json={"task_id": _TASK_ID, "notes": ""},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_422


@pytest.mark.asyncio
async def test_unclaim_dispatches() -> None:
    mock_chore = MagicMock()
    mock_chore.unclaim = AsyncMock(
        return_value=_make_envelope(status="awaiting_pm_review", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v1/flow/cell_pm/unclaim",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_chore.unclaim.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_dispatches() -> None:
    mock_chore = MagicMock()
    mock_chore.resume = AsyncMock(
        return_value=_make_envelope(status="in_progress", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v1/flow/cell_pm/resume",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_chore.resume.assert_awaited_once()
