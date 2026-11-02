"""A write that names no task_id attaches only to the task the caller holds.

``note`` / ``dm`` / ``notify`` without a ``task_id`` used to attach to the
caller's most recently updated assigned task, with no claim check. After an
explicit write was refused, agents re-sent it without ``task_id`` and it
landed on an unrelated task (live 2026-10-28: main-pm's dm about root
be2145b0 went out on 7e74d7ce, be-pm's about 6d4baecf on dd60608d), while the
very same write with that task's id would have been refused.

Now the write attaches to the one task whose claim the caller holds; when it
holds none (but has assigned work) or several, it is refused with the
candidate task ids. Claim-less roles (auditor, board) keep attaching to their
one assigned task, and their task-less observations and broadcasts stay
task-less when they have several.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, JournalEntryTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.task import TaskCreateRequest
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.journal import JournalService
from roboco.services.task import TaskService
from roboco.utils.converters import require_uuid
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from roboco.db.tables import TaskTable
    from sqlalchemy.ext.asyncio import AsyncSession


def _agent(role: AgentRole, slug: str, team: Team) -> AgentTable:
    return AgentTable(
        id=uuid4(),
        name=slug,
        slug=f"{slug}-{uuid4().hex[:8]}",
        role=role,
        team=team,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt=slug,
        capabilities=[],
        permissions={},
        metrics={},
    )


@pytest_asyncio.fixture
async def fleet(db_session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
    agents = {
        "dev": _agent(AgentRole.DEVELOPER, "be-dev", Team.BACKEND),
        "pm": _agent(AgentRole.CELL_PM, "be-pm", Team.BACKEND),
        "main_pm": _agent(AgentRole.MAIN_PM, "main-pm", Team.MAIN_PM),
        "auditor": _agent(AgentRole.AUDITOR, "auditor", Team.BOARD),
        "po": _agent(AgentRole.PRODUCT_OWNER, "product-owner", Team.BOARD),
    }
    db_session.add_all(agents.values())
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="Cell",
        slug=f"cell-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agents["dev"].id,
    )
    db_session.add(project)
    await db_session.flush()
    svc = TaskService(db_session)
    a2a, notifications = AsyncMock(), AsyncMock()
    yield {
        **agents,
        "svc": svc,
        "db": db_session,
        "a2a": a2a,
        "notifications": notifications,
        "project_id": project.id,
        "content": ContentActions(
            ContentActionsDeps(
                task=svc,
                git=AsyncMock(),
                a2a=a2a,
                journal=JournalService(db_session),
                workspace=AsyncMock(),
                notifications=notifications,
            )
        ),
    }


async def _task(
    fleet: dict[str, Any],
    owner: AgentTable,
    *,
    status: TaskStatus,
    claimed: bool,
    minutes_ago: int,
) -> TaskTable:
    """A task assigned to ``owner``; ``claimed`` puts the claim lock on it."""
    svc: TaskService = fleet["svc"]
    task = await svc.create(
        TaskCreateRequest(
            title="t",
            description="d",
            acceptance_criteria=["ac"],
            team=Team.BACKEND,
            created_by=fleet["main_pm"].id,
            project_id=fleet["project_id"],
            task_type=TaskType.PLANNING,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    task.status = status
    task.assigned_to = owner.id
    task.claimed_by = owner.id if claimed else None
    task.active_claimant_id = owner.id if claimed else None
    task.updated_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    await fleet["db"].flush()
    return task


async def _note(fleet: dict[str, Any], author: AgentTable) -> Any:
    content: ContentActions = fleet["content"]
    return await content.note(
        agent_id=require_uuid(author.id),
        text="Freeze the review-path route as the cross-cell contract.",
        scope="decision",
    )


async def _note_task_ids(fleet: dict[str, Any]) -> list[Any]:
    """The task ids the test's notes were recorded on."""
    db: AsyncSession = fleet["db"]
    rows = await db.execute(
        select(JournalEntryTable.task_id).where(
            JournalEntryTable.title.like("Freeze the review-path route%")
        )
    )
    return [row[0] for row in rows.all()]


@pytest.mark.asyncio
async def test_dm_from_a_pm_holding_several_tasks_is_refused_with_them(
    fleet: dict[str, Any],
) -> None:
    """main-pm's dm about be2145b0 must not go out on 7e74d7ce."""
    working = await _task(
        fleet,
        fleet["main_pm"],
        status=TaskStatus.IN_PROGRESS,
        claimed=True,
        minutes_ago=5,
    )
    parked = await _task(
        fleet,
        fleet["main_pm"],
        status=TaskStatus.PAUSED,
        claimed=True,
        minutes_ago=1,
    )
    content: ContentActions = fleet["content"]

    env = await content.dm(
        agent_id=require_uuid(fleet["main_pm"].id),
        recipient=fleet["pm"].slug,
        text="Freeze GET /api/system-health/review-path as the contract.",
    )

    assert env.error == "incomplete_input"
    assert env.missing == ["task_id"]
    assert str(working.id) in (env.remediate or "")
    assert str(parked.id) in (env.remediate or "")
    fleet["a2a"].send.assert_not_awaited()


@pytest.mark.asyncio
async def test_note_attaches_to_the_held_task_not_the_latest_assigned_one(
    fleet: dict[str, Any],
) -> None:
    held = await _task(
        fleet,
        fleet["dev"],
        status=TaskStatus.IN_PROGRESS,
        claimed=True,
        minutes_ago=30,
    )
    await _task(
        fleet,
        fleet["dev"],
        status=TaskStatus.PENDING,
        claimed=False,
        minutes_ago=1,
    )

    env = await _note(fleet, fleet["dev"])

    assert env.error is None, env.as_dict()
    assert await _note_task_ids(fleet) == [held.id]


@pytest.mark.asyncio
async def test_note_with_no_held_task_but_assigned_work_is_refused(
    fleet: dict[str, Any],
) -> None:
    pending = await _task(
        fleet,
        fleet["dev"],
        status=TaskStatus.PENDING,
        claimed=False,
        minutes_ago=1,
    )

    env = await _note(fleet, fleet["dev"])

    assert env.error == "incomplete_input"
    assert str(pending.id) in (env.remediate or "")
    assert await _note_task_ids(fleet) == []


@pytest.mark.asyncio
async def test_note_from_an_agent_with_no_task_stays_task_less(
    fleet: dict[str, Any],
) -> None:
    env = await _note(fleet, fleet["dev"])

    assert env.error is None, env.as_dict()
    assert await _note_task_ids(fleet) == [None]


@pytest.mark.asyncio
async def test_claimless_auditor_note_attaches_to_its_one_assigned_task(
    fleet: dict[str, Any],
) -> None:
    """The auditor works its program task without claiming it (a9739b1c)."""
    program = await _task(
        fleet,
        fleet["auditor"],
        status=TaskStatus.PENDING,
        claimed=False,
        minutes_ago=1,
    )

    env = await _note(fleet, fleet["auditor"])

    assert env.error is None, env.as_dict()
    assert await _note_task_ids(fleet) == [program.id]


@pytest.mark.asyncio
async def test_claimless_observation_with_several_tasks_stays_task_less(
    fleet: dict[str, Any],
) -> None:
    for minutes_ago in (1, 2):
        await _task(
            fleet,
            fleet["auditor"],
            status=TaskStatus.PENDING,
            claimed=False,
            minutes_ago=minutes_ago,
        )

    env = await _note(fleet, fleet["auditor"])

    assert env.error is None, env.as_dict()
    assert await _note_task_ids(fleet) == [None]


@pytest.mark.asyncio
async def test_board_broadcast_notify_stays_off_task(
    fleet: dict[str, Any],
) -> None:
    for minutes_ago in (1, 2):
        await _task(
            fleet,
            fleet["po"],
            status=TaskStatus.PENDING,
            claimed=False,
            minutes_ago=minutes_ago,
        )
    content: ContentActions = fleet["content"]

    env = await content.notify(
        agent_id=require_uuid(fleet["po"].id),
        target=fleet["main_pm"].slug,
        text="Roadmap review this week: bring the open blocker list.",
    )

    assert env.error is None, env.as_dict()
    sent = fleet["notifications"].send_ack_notification.await_args.kwargs
    assert sent["task_id"] is None


@pytest.mark.asyncio
async def test_handoff_from_a_pm_holding_several_tasks_is_refused(
    fleet: dict[str, Any],
) -> None:
    for minutes_ago in (1, 2):
        await _task(
            fleet,
            fleet["pm"],
            status=TaskStatus.IN_PROGRESS,
            claimed=True,
            minutes_ago=minutes_ago,
        )
    content: ContentActions = fleet["content"]

    env = await content.note(
        agent_id=require_uuid(fleet["pm"].id),
        text="Handoff before delegating.",
        scope="handoff",
        done="Planned the slice.",
        next="be-dev-1 builds the endpoint.",
    )

    assert env.error == "incomplete_input"
    assert env.missing == ["task_id"]
