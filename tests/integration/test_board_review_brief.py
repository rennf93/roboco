from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.tasks import router as tasks_router
from roboco.db.tables import (
    AgentTable,
    JournalEntryTable,
    JournalTable,
    ProjectTable,
    TaskTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import JournalEntryType, TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services.journal import JournalService

_HDR_PM = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _agent(slug: str, role: AgentRole) -> AgentTable:
    return AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=role,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )


@pytest_asyncio.fixture
async def brief_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
    po = _agent(f"product-owner-{uuid4().hex[:4]}", AgentRole.PRODUCT_OWNER)
    hom = _agent(f"head-marketing-{uuid4().hex[:4]}", AgentRole.HEAD_MARKETING)
    dev = _agent(f"be-dev-{uuid4().hex[:4]}", AgentRole.DEVELOPER)
    db_session.add_all([po, hom, dev])
    await db_session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=po.id,
    )
    db_session.add(project)
    await db_session.flush()

    def _task() -> TaskTable:
        t = TaskTable(
            id=uuid4(),
            title="Board task",
            description="d",
            acceptance_criteria=["ac"],
            status=TaskStatus.PENDING,
            priority=2,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            project_id=project.id,
            created_by=po.id,
            team=Team.BOARD,
        )
        db_session.add(t)
        return t

    task = _task()
    other = _task()
    await db_session.flush()

    journals = {a.id: JournalTable(id=uuid4(), agent_id=a.id) for a in (po, hom, dev)}
    db_session.add_all(journals.values())
    await db_session.flush()

    def _entry(
        agent: AgentTable,
        *,
        entry_type: JournalEntryType,
        task_id,
        title: str,
        when: datetime,
    ) -> None:
        db_session.add(
            JournalEntryTable(
                id=uuid4(),
                journal_id=journals[agent.id].id,
                type=entry_type,
                title=title,
                content=f"{title} — body",
                task_id=task_id,
                timestamp=when,
                tags=["decision"],
            )
        )

    # HoM logs after PO so we can assert oldest-first ordering returns PO then HoM.
    _entry(
        po,
        entry_type=JournalEntryType.DECISION_LOG,
        task_id=task.id,
        title="PO review",
        when=datetime(2026, 1, 1, tzinfo=UTC),
    )
    _entry(
        hom,
        entry_type=JournalEntryType.DECISION_LOG,
        task_id=task.id,
        title="HoM review",
        when=datetime(2026, 1, 2, tzinfo=UTC),
    )
    # Noise that must be excluded:
    _entry(  # non-board author, decision log
        dev,
        entry_type=JournalEntryType.DECISION_LOG,
        task_id=task.id,
        title="Dev decision",
        when=datetime(2026, 1, 3, tzinfo=UTC),
    )
    _entry(  # board author, but not a decision log
        po,
        entry_type=JournalEntryType.TASK_REFLECTION,
        task_id=task.id,
        title="PO reflection",
        when=datetime(2026, 1, 4, tzinfo=UTC),
    )
    _entry(  # board decision log, but on a different task
        po,
        entry_type=JournalEntryType.DECISION_LOG,
        task_id=other.id,
        title="PO review of other task",
        when=datetime(2026, 1, 5, tzinfo=UTC),
    )
    await db_session.flush()

    yield {"svc": JournalService(db_session), "task": task, "other": other}


@pytest.mark.asyncio
async def test_brief_returns_only_board_decision_logs_oldest_first(
    brief_setup: dict,
) -> None:
    brief = await brief_setup["svc"].board_review_brief(brief_setup["task"].id)
    titles = [e["title"] for e in brief]
    assert titles == ["PO review", "HoM review"]  # ordered, filtered
    assert brief[0]["author_role"] == "product_owner"
    assert brief[1]["author_role"] == "head_marketing"
    assert all("body" in e["content"] for e in brief)


@pytest.mark.asyncio
async def test_brief_empty_when_no_board_review(brief_setup: dict) -> None:
    # `other` only has a single PO decision log on it (created as noise above),
    # so build a genuinely un-reviewed task to assert the empty case.
    fresh = TaskTable(
        id=uuid4(),
        title="Unreviewed",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        created_by=brief_setup["task"].created_by,
        team=Team.BOARD,
    )
    brief_setup["svc"].session.add(fresh)
    await brief_setup["svc"].session.flush()
    assert await brief_setup["svc"].board_review_brief(fresh.id) == []


def _board_review_app(db_session: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/tasks")

    async def _db():
        yield db_session

    async def _agent() -> AgentContext:
        return AgentContext(agent_id=uuid4(), role=AgentRole.MAIN_PM, team=None)

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_agent_context] = _agent
    return app


@pytest.mark.asyncio
async def test_board_review_endpoint_returns_entries(
    brief_setup: dict, db_session: AsyncSession
) -> None:
    app = _board_review_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/tasks/{brief_setup['task'].id}/board-review", headers=_HDR_PM
        )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert [e["title"] for e in body] == ["PO review", "HoM review"]
    assert body[0]["author_role"] == "product_owner"


@pytest.mark.asyncio
async def test_board_review_endpoint_404_for_missing_task(
    db_session: AsyncSession,
) -> None:
    app = _board_review_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/tasks/{uuid4()}/board-review", headers=_HDR_PM)
    assert resp.status_code == HTTPStatus.NOT_FOUND
