"""GET /api/tasks/{id}/findings — the revision-findings ledger read route.

Read-only feed for the panel's Findings tab: newest round first, plus
per-origin status-count summary. Mirrors test_tasks_route_privileged_fields.py's
fixture shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.tasks import router as tasks_router
from roboco.db.tables import AgentTable, ProjectTable, TaskReviewFindingTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def findings_client(db_session: AsyncSession) -> AsyncIterator[dict]:
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="TF-Proj",
        slug=f"tf-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/tf.git",
        assigned_cell=Team.BACKEND,
        created_by=pm.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/tasks")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", pm.id), role=AgentRole.MAIN_PM, team=None
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": pm, "project": project, "db": db_session}
    app.dependency_overrides.clear()


def _seed_task(setup: dict, **kw: Any) -> TaskTable:
    task = TaskTable(
        id=uuid4(),
        title=kw.pop("title", "t"),
        description=kw.pop("description", "d"),
        acceptance_criteria=["ac"],
        status=kw.pop("status", TaskStatus.NEEDS_REVISION),
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=setup["project"].id,
        created_by=setup["agent"].id,
        team=Team.BACKEND,
    )
    setup["db"].add(task)
    return task


def _seed_finding(
    task_id: Any, *, round: int, origin: str, status: str
) -> TaskReviewFindingTable:
    return TaskReviewFindingTable(
        id=uuid4(),
        task_id=task_id,
        origin=origin,
        round=round,
        author_slug="be-qa",
        file="roboco/services/task.py",
        line=42,
        severity="major",
        criterion=None,
        expected="the endpoint returns 404",
        actual="the endpoint returns 500",
        fix="add a not-found guard",
        evidence=None,
        status=status,
        created_at=datetime.now(UTC),
    )


_HDR = {"X-Agent-ID": "ignored", "X-Agent-Role": "main_pm"}


@pytest.mark.asyncio
async def test_findings_404_for_missing_task(findings_client: dict) -> None:
    client = findings_client["client"]
    response = await client.get(f"/api/tasks/{uuid4()}/findings", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_findings_empty_for_never_bounced_task(findings_client: dict) -> None:
    client = findings_client["client"]
    task = _seed_task(findings_client, status=TaskStatus.IN_PROGRESS)
    await findings_client["db"].flush()
    response = await client.get(f"/api/tasks/{task.id}/findings", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["findings"] == []
    assert body["summary"] == []
    assert body["total"] == 0
    assert body["truncated"] is False


@pytest.mark.asyncio
async def test_findings_newest_round_first_with_summary(findings_client: dict) -> None:
    client = findings_client["client"]
    task = _seed_task(findings_client)
    await findings_client["db"].flush()
    findings_client["db"].add_all(
        [
            _seed_finding(task.id, round=1, origin="qa", status="verified"),
            _seed_finding(task.id, round=2, origin="pr_gate", status="open"),
        ]
    )
    await findings_client["db"].flush()

    response = await client.get(f"/api/tasks/{task.id}/findings", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    body = response.json()

    assert [f["round"] for f in body["findings"]] == [2, 1]
    assert body["findings"][0]["origin"] == "pr_gate"
    assert body["findings"][0]["status"] == "open"
    assert body["findings"][0]["expected"] == "the endpoint returns 404"

    summary_by_origin = {s["origin"]: s for s in body["summary"]}
    assert summary_by_origin["qa"] == {
        "origin": "qa",
        "open": 0,
        "addressed": 0,
        "verified": 1,
        "waived": 0,
    }
    assert summary_by_origin["pr_gate"]["open"] == 1
    assert body["total"] == len(body["findings"])
    assert body["truncated"] is False


_LIST_CAP = 500


@pytest.mark.asyncio
async def test_findings_summary_survives_list_truncation(
    findings_client: dict,
) -> None:
    """Past the 500-row list cap, summary/total come from SQL aggregates over
    the WHOLE ledger and truncated flags the capped list — the counts must
    never be silently wrong for a big ledger."""
    client = findings_client["client"]
    task = _seed_task(findings_client)
    await findings_client["db"].flush()
    findings_client["db"].add_all(
        [
            _seed_finding(task.id, round=1, origin="qa", status="open")
            for _ in range(_LIST_CAP + 1)
        ]
    )
    await findings_client["db"].flush()

    response = await client.get(f"/api/tasks/{task.id}/findings", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    body = response.json()

    assert len(body["findings"]) == _LIST_CAP
    assert body["total"] == _LIST_CAP + 1
    assert body["truncated"] is True
    assert body["summary"] == [
        {
            "origin": "qa",
            "open": _LIST_CAP + 1,
            "addressed": 0,
            "verified": 0,
            "waived": 0,
        }
    ]
