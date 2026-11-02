"""A write on a task nobody has claimed yet is refused with the claim verb to use.

The content-write gate refused every write without the active claim with
one message, "you do not hold the active claim on X; another agent owns it
now or your claim was released", and one remedy, "call i_am_idle() and
give_me_work()". For a task assigned to the caller and never claimed (a
PM's fresh root, a developer's pre-assigned leaf, a PM's own task bounced to
needs_revision) both were false: nobody owned it, and the fix was to claim
it. Live 2026-10-28 and 2026-11-01: main-pm on 94ef769a, 4559be66, 28caf535,
1aa61ef1, e9600a89, 7e74d7ce, 99e916a8, 0c4e2592, 14c9875a; the cell PMs on
b2b4d1dc, 241582c0, c55a41e9, 3b7dc263, e7f28774, 69713246, 4674f0a6,
650a20d8 and on their bounced d6a26717, 300472d1, c3a63a00; the developers
on dfeb2d8a, 86b799b8, 0f391d46 and the leaves they could not claim. Several
then obeyed the remedy: i_am_idle refused ("assigned but never claimed"),
or the agent idled and the respawn breaker counted another wasted spawn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.task import TaskCreateRequest
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.journal import JournalService
from roboco.services.task import TaskService
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from roboco.db.tables import TaskTable
    from roboco.services.gateway.envelope import Envelope
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
    dev = _agent(AgentRole.DEVELOPER, "be-dev", Team.BACKEND)
    other_dev = _agent(AgentRole.DEVELOPER, "be-dev", Team.BACKEND)
    pm = _agent(AgentRole.CELL_PM, "be-pm", Team.BACKEND)
    main_pm = _agent(AgentRole.MAIN_PM, "main-pm", Team.MAIN_PM)
    db_session.add_all([dev, other_dev, pm, main_pm])
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="Cell",
        slug=f"cell-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=dev.id,
    )
    db_session.add(project)
    await db_session.flush()
    svc = TaskService(db_session)
    yield {
        "svc": svc,
        "content": ContentActions(
            ContentActionsDeps(
                task=svc,
                git=AsyncMock(),
                a2a=AsyncMock(),
                journal=JournalService(db_session),
                workspace=AsyncMock(),
                notifications=AsyncMock(),
            )
        ),
        "db": db_session,
        "dev": dev,
        "other_dev": other_dev,
        "pm": pm,
        "main_pm": main_pm,
        "project_id": project.id,
    }


async def _assigned_task(
    fleet: dict[str, Any],
    owner: AgentTable,
    *,
    status: TaskStatus,
    task_type: TaskType,
    team: Team,
) -> TaskTable:
    """A task assigned to ``owner`` that nobody holds a claim on."""
    svc: TaskService = fleet["svc"]
    task = await svc.create(
        TaskCreateRequest(
            title="Panel: let the CEO retire approved playbooks",
            description="d",
            acceptance_criteria=["ac"],
            team=team,
            created_by=fleet["main_pm"].id,
            project_id=fleet["project_id"],
            task_type=task_type,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    task.status = status
    task.assigned_to = owner.id
    task.claimed_by = owner.id if status != TaskStatus.PENDING else None
    task.active_claimant_id = None
    await fleet["db"].flush()
    return task


async def _decision_note(
    fleet: dict[str, Any], author: AgentTable, task: TaskTable
) -> Envelope:
    content: ContentActions = fleet["content"]
    return await content.note(
        agent_id=require_uuid(author.id),
        text="Route the whole item to one cell; no backend or UX slice.",
        scope="decision",
        task_id=require_uuid(task.id),
    )


@pytest.mark.asyncio
async def test_main_pm_note_on_its_fresh_root_names_i_will_plan(
    fleet: dict[str, Any],
) -> None:
    root = await _assigned_task(
        fleet,
        fleet["main_pm"],
        status=TaskStatus.PENDING,
        task_type=TaskType.PLANNING,
        team=Team.MAIN_PM,
    )

    env = await _decision_note(fleet, fleet["main_pm"], root)

    assert env.error == "not_authorized"
    assert "active claim" in (env.message or "")
    assert "another agent" not in (env.message or "")
    assert f"i_will_plan(task_id='{root.id}'" in (env.remediate or "")
    assert "i_am_idle" not in (env.remediate or "")
    assert env.current_state == "pending"
    assert "i_will_plan" in (env.valid_next_verbs or [])


@pytest.mark.asyncio
async def test_cell_pm_note_on_its_bounced_task_names_i_will_plan(
    fleet: dict[str, Any],
) -> None:
    """fe-pm on d6a26717 and be-pm on 300472d1 after pr_fail."""
    bounced = await _assigned_task(
        fleet,
        fleet["pm"],
        status=TaskStatus.NEEDS_REVISION,
        task_type=TaskType.PLANNING,
        team=Team.BACKEND,
    )

    env = await _decision_note(fleet, fleet["pm"], bounced)

    assert env.error == "not_authorized"
    assert f"i_will_plan(task_id='{bounced.id}'" in (env.remediate or "")


@pytest.mark.asyncio
async def test_developer_dm_about_its_unclaimed_leaf_names_i_will_work_on(
    fleet: dict[str, Any],
) -> None:
    leaf = await _assigned_task(
        fleet,
        fleet["dev"],
        status=TaskStatus.PENDING,
        task_type=TaskType.CODE,
        team=Team.BACKEND,
    )
    content: ContentActions = fleet["content"]

    env = await content.dm(
        agent_id=require_uuid(fleet["dev"].id),
        recipient=fleet["pm"].slug,
        text="I cannot claim this leaf yet; I hold another task.",
        task_id=require_uuid(leaf.id),
    )

    assert env.error == "not_authorized"
    assert f"i_will_work_on(task_id='{leaf.id}'" in (env.remediate or "")


@pytest.mark.asyncio
async def test_write_under_another_agents_claim_still_sends_the_caller_away(
    fleet: dict[str, Any],
) -> None:
    """The reaped-and-reclaimed shape keeps its refusal and its remedy."""
    leaf = await _assigned_task(
        fleet,
        fleet["dev"],
        status=TaskStatus.IN_PROGRESS,
        task_type=TaskType.CODE,
        team=Team.BACKEND,
    )
    leaf.active_claimant_id = fleet["other_dev"].id
    await fleet["db"].flush()

    env = await _decision_note(fleet, fleet["dev"], leaf)

    assert env.error == "not_authorized"
    assert "another agent holds" in (env.message or "")
    assert "give_me_work()" in (env.remediate or "")
