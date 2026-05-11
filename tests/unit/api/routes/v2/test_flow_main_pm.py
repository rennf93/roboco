"""Unit tests for /api/v2/flow/main_pm/* endpoints.

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
from roboco.api.routes.v2.flow_main_pm import router

_HTTP_200 = 200
_HTTP_422 = 422

_AGENT_ID = str(uuid4())
_TASK_ID = str(uuid4())
_HEADERS = {"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "main_pm"}


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
    """Build minimal FastAPI app with the flow_main_pm router and a mocked dep."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_choreographer] = lambda: mock_choreographer
    return app


@pytest.mark.asyncio
async def test_triage_all_returns_envelope() -> None:
    """POST /api/v2/flow/main_pm/triage_all returns 200 with task or idle status."""
    mock_chore = MagicMock()
    mock_chore.triage_all = AsyncMock(
        return_value=_make_envelope(status="awaiting_pm_review", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/main_pm/triage_all",
        json={},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "awaiting_pm_review"
    mock_chore.triage_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_calls_main_pm_complete_directly() -> None:
    """POST /api/v2/flow/main_pm/complete calls main_pm_complete (not dispatch)."""
    mock_chore = MagicMock()
    mock_chore.main_pm_complete = AsyncMock(
        return_value=_make_envelope(status="awaiting_ceo_approval", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/main_pm/complete",
        json={"task_id": _TASK_ID, "notes": "Root task done, escalating to CEO."},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    body = resp.json()
    assert body["status"] == "awaiting_ceo_approval"
    mock_chore.main_pm_complete.assert_awaited_once()
    call_args = mock_chore.main_pm_complete.call_args
    assert str(call_args.args[1]) == _TASK_ID
    assert call_args.args[2] == "Root task done, escalating to CEO."


@pytest.mark.asyncio
async def test_escalate_up_dispatches_reason() -> None:
    """POST /api/v2/flow/main_pm/escalate_up forwards task_id and reason."""
    mock_chore = MagicMock()
    mock_chore.escalate_up = AsyncMock(
        return_value=_make_envelope(status="awaiting_ceo_approval", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/main_pm/escalate_up",
        json={"task_id": _TASK_ID, "reason": "Needs CEO sign-off on architecture."},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.escalate_up.assert_awaited_once()
    call_args = mock_chore.escalate_up.call_args
    assert call_args.args[2] == "Needs CEO sign-off on architecture."


@pytest.mark.asyncio
async def test_unblock_dispatches_task_id_with_restore_true() -> None:
    """POST /api/v2/flow/main_pm/unblock forwards task_id with restore=True default."""
    mock_chore = MagicMock()
    mock_chore.unblock = AsyncMock(
        return_value=_make_envelope(status="in_progress", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/main_pm/unblock",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.unblock.assert_awaited_once()
    call_kwargs = mock_chore.unblock.call_args.kwargs
    assert call_kwargs["restore"] is True


@pytest.mark.asyncio
async def test_i_am_idle_dispatches_agent_id() -> None:
    """POST /api/v2/flow/main_pm/i_am_idle delegates to Choreographer.i_am_idle."""
    mock_chore = MagicMock()
    mock_chore.i_am_idle = AsyncMock(return_value=_make_envelope(status="idle"))
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/main_pm/i_am_idle",
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
        "/api/v2/flow/main_pm/complete",
        json={"task_id": _TASK_ID, "notes": ""},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_422


def test_escalate_up_rejects_empty_reason() -> None:
    """POST escalate_up rejects empty reason (min_length=1)."""
    mock_chore = MagicMock()
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/main_pm/escalate_up",
        json={"task_id": _TASK_ID, "reason": ""},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_422


@pytest.mark.asyncio
async def test_give_me_work_routes_to_pm_give_me_work() -> None:
    """POST /api/v2/flow/main_pm/give_me_work delegates to pm_give_me_work."""
    mock_chore = MagicMock()
    mock_chore.pm_give_me_work = AsyncMock(return_value=_make_envelope(status="idle"))
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/main_pm/give_me_work",
        json={},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.pm_give_me_work.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_will_plan_dispatches_to_choreographer() -> None:
    """POST /api/v2/flow/main_pm/i_will_plan forwards task_id and plan."""
    mock_chore = MagicMock()
    mock_chore.i_will_plan = AsyncMock(
        return_value=_make_envelope(status="in_progress", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/main_pm/i_will_plan",
        json={"task_id": _TASK_ID, "plan": "split into backend, frontend, ux cells"},
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.i_will_plan.assert_awaited_once()


@pytest.mark.asyncio
async def test_delegate_to_cell_pm_dispatches_inputs_bundle() -> None:
    """POST /api/v2/flow/main_pm/delegate forwards body via DelegateInputs."""
    mock_chore = MagicMock()
    mock_chore.delegate = AsyncMock(
        return_value=_make_envelope(status="created", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))

    resp = client.post(
        "/api/v2/flow/main_pm/delegate",
        json={
            "parent_task_id": _TASK_ID,
            "title": "Backend slice",
            "description": "Plan + drive backend work for feature X end to end.",
            "assigned_to": "be-pm",
            "team": "backend",
            "task_type": "planning",
            "nature": "technical",
            "estimated_complexity": "high",
            "acceptance_criteria": [
                "all subtasks created with acceptance criteria",
                "branch + PR opened against the slice",
            ],
        },
        headers=_HEADERS,
    )

    assert resp.status_code == _HTTP_200
    mock_chore.delegate.assert_awaited_once()
    inputs = mock_chore.delegate.await_args.args[2]
    assert inputs.task_type == "planning"


@pytest.mark.asyncio
async def test_escalate_to_ceo_dispatches() -> None:
    mock_chore = MagicMock()
    mock_chore.escalate_to_ceo = AsyncMock(
        return_value=_make_envelope(status="awaiting_ceo_approval", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v2/flow/main_pm/escalate_to_ceo",
        json={"task_id": _TASK_ID, "reason": "needs CEO sign-off"},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_chore.escalate_to_ceo.assert_awaited_once()


@pytest.mark.asyncio
async def test_unclaim_dispatches() -> None:
    mock_chore = MagicMock()
    mock_chore.unclaim = AsyncMock(
        return_value=_make_envelope(status="awaiting_pm_review", task_id=_TASK_ID)
    )
    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v2/flow/main_pm/unclaim",
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
        "/api/v2/flow/main_pm/resume",
        json={"task_id": _TASK_ID},
        headers=_HEADERS,
    )
    assert resp.status_code == _HTTP_200
    mock_chore.resume.assert_awaited_once()
