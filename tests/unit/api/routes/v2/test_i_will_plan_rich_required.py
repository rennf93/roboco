"""Wave A1: i_will_plan rejects PM claims that lack the rich plan shape.

Pre-gateway parity for `_validate_claimed_start` — agents could not
transition claimed → in_progress without filling approach + sub_tasks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from roboco.api.deps import get_choreographer
from roboco.api.routes.v2.flow_main_pm import router
from roboco.api.schemas.v2.flow import IWillPlanRequest

_AGENT_ID = "00000000-0000-0000-0004-000000000001"
_HEADERS = {"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "main_pm"}
_HTTP_UNPROCESSABLE = 422
_HTTP_OK = 200
_MIN_APPROACH_LEN = 20


def _make_envelope(error: str, missing: list[str] | None = None) -> MagicMock:
    env = MagicMock()
    payload: dict = {"error": error}
    if missing is not None:
        payload["missing"] = missing
    env.as_dict.return_value = payload
    env.correlation_id = None
    return env


def _build_app(mock_choreographer: MagicMock | None = None) -> FastAPI:
    """Minimal FastAPI app with the flow_main_pm router and optional mocked dep."""
    app = FastAPI()
    app.include_router(router)
    if mock_choreographer is not None:
        app.dependency_overrides[get_choreographer] = lambda: mock_choreographer
    return app


def test_i_will_plan_rejects_missing_approach() -> None:
    """A PM calling i_will_plan with bare `plan` (no approach) is rejected."""
    client = TestClient(_build_app())
    resp = client.post(
        "/api/v2/flow/main_pm/i_will_plan",
        headers=_HEADERS,
        json={
            "task_id": str(uuid4()),
            "plan": "I will route this to backend cell",
            # no approach, no sub_tasks
        },
    )
    assert resp.status_code == _HTTP_UNPROCESSABLE, resp.text
    detail = resp.json()
    assert any(
        "approach" in str(err.get("loc", [])) for err in detail.get("detail", [])
    ), detail


def test_i_will_plan_rejects_empty_subtasks_for_pm() -> None:
    """Approach satisfied but sub_tasks empty -> gateway rejects via incomplete_input.

    Devs are NOT required to have sub_tasks; PMs are.
    """
    mock_chore = MagicMock()
    incomplete_env = _make_envelope(error="incomplete_input", missing=["sub_tasks"])
    mock_chore.i_will_plan = AsyncMock(return_value=incomplete_env)

    client = TestClient(_build_app(mock_chore))
    resp = client.post(
        "/api/v2/flow/main_pm/i_will_plan",
        headers=_HEADERS,
        json={
            "task_id": str(uuid4()),
            "plan": "Route to backend cell only",
            "approach": (
                "Single-cell decomposition: backend cell handles the "
                "smoke test end-to-end; frontend and ux unaffected."
            ),
            "sub_tasks": [],  # empty — gateway rejects for PM
        },
    )
    # Schema validation passes (sub_tasks is a valid empty list).
    # The gateway-side gate returns an envelope with error='incomplete_input'
    # and missing=['sub_tasks']. HTTP status is 200 (envelope carries error).
    body = resp.json()
    if resp.status_code == _HTTP_OK:
        assert body.get("error") == "incomplete_input", body
        assert "sub_tasks" in (body.get("missing") or []), body
    else:
        # 422 or other 4xx; confirm sub_tasks is mentioned
        text = resp.text.lower()
        assert "sub_tasks" in text, text


def test_i_will_plan_schema_accepts_rich_plan() -> None:
    """Pydantic schema accepts a fully-formed request with rich plan fields."""
    req = IWillPlanRequest(
        task_id=uuid4(),
        plan="Route to backend",
        approach=(
            "Single-cell decomposition for the smoke test: be-pm handles "
            "git workflow validation end to end."
        ),
        sub_tasks=[{"title": "Backend slice", "description": "Branch + edit + PR"}],
        risks=[],
        open_questions=[],
    )
    assert len(req.approach) >= _MIN_APPROACH_LEN
    assert len(req.sub_tasks) == 1
