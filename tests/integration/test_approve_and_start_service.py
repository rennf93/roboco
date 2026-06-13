from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def start_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
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

    main_pm = _agent("main-pm", AgentRole.MAIN_PM)
    po = _agent(f"product-owner-{uuid4().hex[:4]}", AgentRole.PRODUCT_OWNER)
    db_session.add_all([main_pm, po])
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

    def _task(
        status: TaskStatus = TaskStatus.PENDING,
        assigned_to: UUID | None = None,
        team: Team = Team.MAIN_PM,
        board_review_complete: bool = False,
    ) -> TaskTable:
        t = TaskTable(
            id=uuid4(),
            title="Board task",
            description="d",
            acceptance_criteria=["ac"],
            status=status,
            priority=2,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            project_id=project.id,
            created_by=po.id,
            team=team,
            board_review_complete=board_review_complete,
            assigned_to=assigned_to if assigned_to else po.id,
        )
        db_session.add(t)
        return t

    yield {
        "svc": TaskService(db_session),
        "main_pm": main_pm,
        "po": po,
        "db": db_session,
        "mk": _task,
    }


@pytest.mark.asyncio
async def test_reassigns_to_main_pm_and_keeps_pending(start_setup: dict) -> None:
    task = start_setup["mk"]()
    await start_setup["db"].flush()
    note = "Board review complete; requirements are clear. Build it."
    out = await start_setup["svc"].approve_and_start(task.id, note)
    assert out is not None
    assert out.assigned_to == start_setup["main_pm"].id
    assert out.status == TaskStatus.PENDING
    assert out.quick_context is not None
    assert "approve_and_start_notes:" in out.quick_context
    assert note in out.quick_context


@pytest.mark.asyncio
async def test_audit_note_appends_to_existing_context(start_setup: dict) -> None:
    task = start_setup["mk"]()
    task.quick_context = "prior context"
    await start_setup["db"].flush()
    note = "Board signed off; ship it."
    out = await start_setup["svc"].approve_and_start(task.id, note)
    assert out is not None
    assert out.quick_context is not None
    assert "prior context" in out.quick_context
    assert f"approve_and_start_notes:{note}" in out.quick_context


@pytest.mark.asyncio
async def test_idempotent_when_already_main_pm(start_setup: dict) -> None:
    task = start_setup["mk"](assigned_to=start_setup["main_pm"].id)
    await start_setup["db"].flush()
    out = await start_setup["svc"].approve_and_start(task.id, "x" * 25)
    assert out is not None
    assert out.assigned_to == start_setup["main_pm"].id
    assert out.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_returns_none_when_not_pending(start_setup: dict) -> None:
    task = start_setup["mk"](status=TaskStatus.IN_PROGRESS)
    await start_setup["db"].flush()
    out = await start_setup["svc"].approve_and_start(task.id, "x" * 25)
    assert out is None


@pytest.mark.asyncio
async def test_returns_none_when_board_review_incomplete(start_setup: dict) -> None:
    # A task still on the board with an unfinished review must not be started.
    task = start_setup["mk"](team=Team.BOARD, board_review_complete=False)
    await start_setup["db"].flush()
    out = await start_setup["svc"].approve_and_start(task.id, "x" * 25)
    assert out is None
    assert task.assigned_to == start_setup["po"].id  # not handed to Main PM


@pytest.mark.asyncio
async def test_succeeds_when_board_review_complete(start_setup: dict) -> None:
    task = start_setup["mk"](team=Team.BOARD, board_review_complete=True)
    await start_setup["db"].flush()
    out = await start_setup["svc"].approve_and_start(task.id, "x" * 25)
    assert out is not None
    assert out.assigned_to == start_setup["main_pm"].id
    assert out.status == TaskStatus.PENDING
