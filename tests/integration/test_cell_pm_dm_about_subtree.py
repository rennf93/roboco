"""A cell PM may dm the assignee of a task in its own subtree about that task.

The content-write gate admitted a ``dm`` with an explicit ``task_id`` only from
the task's assignee, so a cell PM could not message its own developer about the
developer's task: on 2026-10-28 fe-pm unblocked 86ed0eab (fe-dev-1's leaf under
fe-pm's cell task) four times and each time its ``dm('fe-dev-1',
task_id=86ed0eab)`` explaining the unblock was refused with "you are not the
assignee"; the agent then resent it task-less and it landed on another task.
The CEO's rule (2026-11-01): a cell PM may dm about any task in its own subtree
to that task's assignee in its own cell. Main PM to a cell developer stays
refused (cross-cell), as does a PM writing to another cell's developer, and
their refusals now name the route that works.

The agents carry the fleet's slugs so the real A2A policy (``A2AService.send``
-> ``validate_a2a_access``) runs on every dm.
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
from roboco.services.a2a import A2AService
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.journal import JournalService
from roboco.services.task import TaskService
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from roboco.db.tables import TaskTable
    from roboco.services.gateway.envelope import Envelope
    from sqlalchemy.ext.asyncio import AsyncSession


def _agent(slug: str, role: AgentRole, team: Team) -> AgentTable:
    return AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
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
        "main-pm": _agent("main-pm", AgentRole.MAIN_PM, Team.MAIN_PM),
        "fe-pm": _agent("fe-pm", AgentRole.CELL_PM, Team.FRONTEND),
        "fe-dev-1": _agent("fe-dev-1", AgentRole.DEVELOPER, Team.FRONTEND),
        "fe-qa": _agent("fe-qa", AgentRole.QA, Team.FRONTEND),
        "be-pm": _agent("be-pm", AgentRole.CELL_PM, Team.BACKEND),
    }
    db_session.add_all(agents.values())
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="Panel",
        slug=f"panel-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.FRONTEND,
        created_by=agents["main-pm"].id,
    )
    db_session.add(project)
    await db_session.flush()
    svc = TaskService(db_session)
    content = ContentActions(
        ContentActionsDeps(
            task=svc,
            git=AsyncMock(),
            a2a=A2AService(db_session),
            journal=JournalService(db_session),
            workspace=AsyncMock(),
            notifications=AsyncMock(),
        )
    )
    fleet = {"svc": svc, "content": content, "db": db_session, **agents}
    fleet["project_id"] = project.id
    root = await _task(fleet, owner="main-pm", team=Team.MAIN_PM, parent=None)
    cell_task = await _task(fleet, owner="fe-pm", team=Team.FRONTEND, parent=root)
    leaf = await _task(fleet, owner="fe-dev-1", team=Team.FRONTEND, parent=cell_task)
    be_task = await _task(fleet, owner="be-pm", team=Team.BACKEND, parent=root)
    yield {
        **fleet,
        "root": root,
        "cell_task": cell_task,
        "leaf": leaf,
        "be_task": be_task,
    }


async def _task(
    fleet: dict[str, Any],
    *,
    owner: str,
    team: Team,
    parent: TaskTable | None,
) -> TaskTable:
    """An in-progress task its owner holds the claim on; the coordination
    levels (the root and each cell task) are planning, the leaf is code."""
    svc: TaskService = fleet["svc"]
    task = await svc.create(
        TaskCreateRequest(
            title=f"{owner} task",
            description="d",
            acceptance_criteria=["ac"],
            team=team,
            created_by=fleet["main-pm"].id,
            project_id=fleet["project_id"],
            task_type=TaskType.CODE
            if parent and parent.parent_task_id
            else TaskType.PLANNING,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    agent: AgentTable = fleet[owner]
    task.parent_task_id = parent.id if parent is not None else None
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_to = agent.id
    task.claimed_by = agent.id
    task.active_claimant_id = agent.id
    await fleet["db"].flush()
    return task


async def _dm(
    fleet: dict[str, Any], sender: str, recipient: str, task: TaskTable
) -> Envelope:
    content: ContentActions = fleet["content"]
    return await content.dm(
        agent_id=require_uuid(fleet[sender].id),
        recipient=recipient,
        text="86ed0eab is unblocked and back with you; revert the stray commit.",
        task_id=require_uuid(task.id),
    )


@pytest.mark.asyncio
async def test_cell_pm_dms_the_assignee_of_a_task_in_its_subtree(
    fleet: dict[str, Any],
) -> None:
    """fe-pm -> fe-dev-1 about 86ed0eab, refused four times on 2026-10-28."""
    env = await _dm(fleet, "fe-pm", "fe-dev-1", fleet["leaf"])

    assert env.error is None, env.as_dict()
    assert env.task_id == str(fleet["leaf"].id)


@pytest.mark.asyncio
async def test_cell_pm_still_writes_only_to_the_tasks_assignee(
    fleet: dict[str, Any],
) -> None:
    """The subtree rule reaches the task's assignee, not the rest of the cell."""
    env = await _dm(fleet, "fe-pm", "fe-qa", fleet["leaf"])

    assert env.error == "not_authorized"
    assert "fe-dev-1" in (env.remediate or "")


@pytest.mark.asyncio
async def test_main_pm_to_a_cell_developer_stays_refused_and_names_the_cell_pm(
    fleet: dict[str, Any],
) -> None:
    """Main PM -> cell developer is cross-cell, about the leaf or its own root."""
    about_leaf = await _dm(fleet, "main-pm", "fe-dev-1", fleet["leaf"])
    about_root = await _dm(fleet, "main-pm", "fe-dev-1", fleet["root"])

    assert about_leaf.error == "not_authorized"
    assert about_root.error == "not_authorized"
    for env in (about_leaf, about_root):
        remediate = env.remediate or ""
        assert "fe-pm" in remediate, env.as_dict()
        assert "None" not in remediate


@pytest.mark.asyncio
async def test_another_cells_pm_to_a_developer_stays_refused_and_names_the_cell_pm(
    fleet: dict[str, Any],
) -> None:
    """be-pm -> fe-dev-1, about fe-dev-1's leaf or about be-pm's own task."""
    about_leaf = await _dm(fleet, "be-pm", "fe-dev-1", fleet["leaf"])
    about_own_task = await _dm(fleet, "be-pm", "fe-dev-1", fleet["be_task"])

    assert about_leaf.error == "not_authorized"
    assert about_own_task.error == "not_authorized"
    for env in (about_leaf, about_own_task):
        remediate = env.remediate or ""
        assert "fe-pm" in remediate, env.as_dict()
        assert "be-pm→be-pm" not in remediate


@pytest.mark.asyncio
async def test_main_pm_reaches_the_developer_through_the_cell_pm(
    fleet: dict[str, Any],
) -> None:
    """The route the refusals name: main-pm -> fe-pm on its root, then
    fe-pm -> fe-dev-1 on the leaf."""
    to_cell_pm = await _dm(fleet, "main-pm", "fe-pm", fleet["root"])
    to_developer = await _dm(fleet, "fe-pm", "fe-dev-1", fleet["leaf"])

    assert to_cell_pm.error is None, to_cell_pm.as_dict()
    assert to_developer.error is None, to_developer.as_dict()
