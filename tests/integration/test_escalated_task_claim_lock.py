"""An escalation hands the escalated task's claim lock to the escalation target.

``TaskService.apply_escalation`` reassigned a task to the PM it was escalated
to (``assigned_to`` and ``claimed_by``) but left ``active_claimant_id`` on the
agent that escalated it. The content-write gate admits only the active
claimant, so the PM that now owned the blocked task had every ``note`` /
``dm`` / ``notify`` on it refused with "you do not hold the active claim".
Live 2026-10-28 and 2026-11-01: 19 refusals, among them be-pm's decision
notes on 11a83407 and f5d66db6 after be-dev-1's ``i_am_blocked``, fe-pm's on
86ed0eab, main-pm's dm to fe-dev-1 about 86ed0eab, and main-pm's notes and
CEO notification about d6a26717 after fe-pm's ``escalate_up``.

These tests drive the real ``TaskService`` escalation and unblock paths and
the real content-write gate (``ContentActions`` over the real task and
journal services) against a database.
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


def _agent(role: AgentRole, slug: str) -> AgentTable:
    return AgentTable(
        id=uuid4(),
        name=slug,
        slug=f"{slug}-{uuid4().hex[:8]}",
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt=slug,
        capabilities=[],
        permissions={},
        metrics={},
    )


@pytest_asyncio.fixture
async def cell(db_session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
    dev = _agent(AgentRole.DEVELOPER, "be-dev")
    pm = _agent(AgentRole.CELL_PM, "be-pm")
    main_pm = _agent(AgentRole.MAIN_PM, "main-pm")
    db_session.add_all([dev, pm, main_pm])
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
    content = ContentActions(
        ContentActionsDeps(
            task=svc,
            git=AsyncMock(),
            a2a=AsyncMock(),
            journal=JournalService(db_session),
            workspace=AsyncMock(),
            notifications=AsyncMock(),
        )
    )
    yield {
        "svc": svc,
        "content": content,
        "journal": JournalService(db_session),
        "db": db_session,
        "dev": dev,
        "pm": pm,
        "main_pm": main_pm,
        "project_id": project.id,
    }


async def _claimed_leaf(cell: dict[str, Any], *, branch: str | None) -> TaskTable:
    """A developer leaf in progress under the developer's live claim."""
    svc: TaskService = cell["svc"]
    task = await svc.create(
        TaskCreateRequest(
            title="Stuck-state fields on task API payload",
            description="d",
            acceptance_criteria=["ac"],
            team=Team.BACKEND,
            created_by=cell["pm"].id,
            project_id=cell["project_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    dev = cell["dev"]
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = dev.id
    task.claimed_by = dev.id
    task.active_claimant_id = dev.id
    task.branch_name = branch
    await cell["db"].flush()
    return task


async def _escalate(
    cell: dict[str, Any], task: TaskTable, *, by: AgentTable, to: AgentTable
) -> None:
    applied = await cell["svc"].apply_escalation(
        task=task,
        target_agent_id=to.id,
        escalator_slug=by.slug,
        target_slug=to.slug,
        reason="needs a PM decision",
    )
    assert applied


async def _decision_note(
    cell: dict[str, Any], author: AgentTable, task: TaskTable
) -> Envelope:
    content: ContentActions = cell["content"]
    return await content.note(
        agent_id=require_uuid(author.id),
        text="Unblock: the block carries no reason and the work is complete.",
        scope="decision",
        task_id=require_uuid(task.id),
    )


@pytest.mark.asyncio
async def test_escalation_hands_the_claim_lock_to_the_target(
    cell: dict[str, Any],
) -> None:
    task = await _claimed_leaf(cell, branch="feature/backend/abc")

    await _escalate(cell, task, by=cell["dev"], to=cell["pm"])

    assert task.status == TaskStatus.BLOCKED
    assert task.assigned_to == cell["pm"].id
    assert task.active_claimant_id == cell["pm"].id


@pytest.mark.asyncio
async def test_escalation_target_records_its_decision_on_the_escalated_task(
    cell: dict[str, Any],
) -> None:
    """be-pm on 11a83407 / f5d66db6: decision note before unblock."""
    task = await _claimed_leaf(cell, branch="feature/backend/abc")
    await _escalate(cell, task, by=cell["dev"], to=cell["pm"])

    env = await _decision_note(cell, cell["pm"], task)

    assert env.error is None, env.as_dict()
    journal: JournalService = cell["journal"]
    assert await journal.has_decision_for_task(
        require_uuid(cell["pm"].id), require_uuid(task.id)
    )


@pytest.mark.asyncio
async def test_escalation_target_messages_about_the_escalated_task(
    cell: dict[str, Any],
) -> None:
    """main-pm's dm about 86ed0eab and its CEO notify about d6a26717."""
    task = await _claimed_leaf(cell, branch="feature/frontend/abc")
    await _escalate(cell, task, by=cell["pm"], to=cell["main_pm"])
    content: ContentActions = cell["content"]
    main_pm, task_id = require_uuid(cell["main_pm"].id), require_uuid(task.id)

    dm = await content.dm(
        agent_id=main_pm,
        recipient=cell["pm"].slug,
        text="Unblocking: revert the out-of-scope generated artifacts.",
        task_id=task_id,
    )
    notify = await content.notify(
        agent_id=main_pm,
        target="ceo",
        text="Please Approve-and-Start the self-heal task for the red base CI.",
        priority="high",
        task_id=task_id,
    )

    assert dm.error is None, dm.as_dict()
    assert notify.error is None, notify.as_dict()


@pytest.mark.asyncio
async def test_re_escalation_moves_the_lock_on_and_locks_out_the_escalator(
    cell: dict[str, Any],
) -> None:
    """fe-pm escalated d6a26717 up to main-pm: the lock follows each hop."""
    task = await _claimed_leaf(cell, branch="feature/frontend/abc")
    await _escalate(cell, task, by=cell["dev"], to=cell["pm"])
    await _escalate(cell, task, by=cell["pm"], to=cell["main_pm"])

    by_main_pm = await _decision_note(cell, cell["main_pm"], task)
    by_cell_pm = await _decision_note(cell, cell["pm"], task)

    assert task.active_claimant_id == cell["main_pm"].id
    assert by_main_pm.error is None, by_main_pm.as_dict()
    assert by_cell_pm.error == "not_authorized"


@pytest.mark.asyncio
async def test_unblock_with_restore_hands_the_lock_back_to_the_developer(
    cell: dict[str, Any],
) -> None:
    task = await _claimed_leaf(cell, branch="feature/backend/abc")
    await _escalate(cell, task, by=cell["dev"], to=cell["pm"])

    await cell["svc"].unblock_with_restore(cell["pm"].id, task.id, restore=True)
    by_dev = await _decision_note(cell, cell["dev"], task)
    by_pm = await _decision_note(cell, cell["pm"], task)

    assert task.status == TaskStatus.IN_PROGRESS
    assert task.active_claimant_id == cell["dev"].id
    assert by_dev.error is None, by_dev.as_dict()
    assert by_pm.error == "not_authorized"


@pytest.mark.asyncio
async def test_legacy_unblock_hands_the_lock_to_the_resumed_owner(
    cell: dict[str, Any],
) -> None:
    """The panel's unblock route (no pre-block snapshot) resumes the raiser."""
    task = await _claimed_leaf(cell, branch="feature/backend/abc")
    await _escalate(cell, task, by=cell["dev"], to=cell["pm"])
    task.pre_block_state = None
    await cell["db"].flush()

    await cell["svc"].unblock(task.id, agent_role="cell_pm")
    by_dev = await _decision_note(cell, cell["dev"], task)

    assert task.status == TaskStatus.IN_PROGRESS
    assert task.assigned_to == cell["dev"].id
    assert task.active_claimant_id == cell["dev"].id
    assert by_dev.error is None, by_dev.as_dict()


@pytest.mark.asyncio
async def test_legacy_unblock_to_pending_leaves_the_task_unclaimed(
    cell: dict[str, Any],
) -> None:
    """A branchless task returns to PENDING and waits for a fresh claim."""
    task = await _claimed_leaf(cell, branch=None)
    await _escalate(cell, task, by=cell["dev"], to=cell["pm"])
    task.pre_block_state = None
    await cell["db"].flush()

    await cell["svc"].unblock(task.id, agent_role="cell_pm")

    assert task.status == TaskStatus.PENDING
    assert task.assigned_to == cell["dev"].id
    assert task.active_claimant_id is None
