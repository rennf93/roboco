"""TaskService.resolve_root_source / is_human_authored: real provenance.

``tasks.source`` alone lies about who originated a delegated task:
``create_subtask`` never forwards a parent's source, so every delegated
subtask's own source reads "manual" regardless of what actually kicked off
the work (a human, or a Board Program proposal, or a self-heal fix). These
tests walk real parent chains against Postgres and assert the root-ancestor
classification instead of the (meaningless, for a subtask) own-source column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _agent() -> AgentTable:
    slug = f"system-{uuid4().hex[:6]}"
    return AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )


def _task(
    project_id: Any,
    created_by: Any,
    *,
    source: str,
    parent_task_id: Any = None,
) -> TaskTable:
    return TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        status=TaskStatus.PENDING,
        team=Team.BACKEND,
        project_id=project_id,
        created_by=created_by,
        estimated_complexity=Complexity.MEDIUM,
        source=source,
        parent_task_id=parent_task_id,
    )


@pytest_asyncio.fixture
async def provenance_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
    agent = _agent()
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "db": db_session,
        "project_id": project.id,
        "agent_id": agent.id,
    }


@pytest.mark.asyncio
async def test_human_root_is_human_authored(provenance_setup: dict) -> None:
    db = provenance_setup["db"]
    root = _task(
        provenance_setup["project_id"], provenance_setup["agent_id"], source="manual"
    )
    db.add(root)
    await db.flush()

    svc = provenance_setup["svc"]
    assert await svc.resolve_root_source(root.id) == "manual"
    assert await svc.is_human_authored(root.id) is True


@pytest.mark.asyncio
async def test_prompter_root_is_human_authored(provenance_setup: dict) -> None:
    db = provenance_setup["db"]
    root = _task(
        provenance_setup["project_id"], provenance_setup["agent_id"], source="prompter"
    )
    db.add(root)
    await db.flush()

    assert await provenance_setup["svc"].is_human_authored(root.id) is True


@pytest.mark.asyncio
async def test_delegated_child_of_human_root_is_human_authored(
    provenance_setup: dict,
) -> None:
    db = provenance_setup["db"]
    root = _task(
        provenance_setup["project_id"], provenance_setup["agent_id"], source="manual"
    )
    db.add(root)
    await db.flush()
    # create_subtask always stamps "manual" on the child regardless of the
    # parent's real source, this mirrors that default, not a hand-picked one.
    child = _task(
        provenance_setup["project_id"],
        provenance_setup["agent_id"],
        source="manual",
        parent_task_id=root.id,
    )
    db.add(child)
    await db.flush()

    svc = provenance_setup["svc"]
    assert await svc.resolve_root_source(child.id) == "manual"
    assert await svc.is_human_authored(child.id) is True


@pytest.mark.asyncio
async def test_delegated_child_of_board_program_root_is_agent_authored(
    provenance_setup: dict,
) -> None:
    """The exact bug scenario: a Mirror-proposed root delegates a subtask,
    the subtask's OWN ``source`` reads "manual" (unchanged, current
    behavior), but provenance correctly resolves to agent-authored because
    the ROOT is a board-program item, not a human."""
    db = provenance_setup["db"]
    root = _task(
        provenance_setup["project_id"], provenance_setup["agent_id"], source="mirror"
    )
    db.add(root)
    await db.flush()
    child = _task(
        provenance_setup["project_id"],
        provenance_setup["agent_id"],
        source="manual",
        parent_task_id=root.id,
    )
    db.add(child)
    await db.flush()

    svc = provenance_setup["svc"]
    # The landmine check: the child's own column is untouched by this fix.
    assert child.source == "manual"
    assert await svc.resolve_root_source(child.id) == "mirror"
    assert await svc.is_human_authored(child.id) is False


@pytest.mark.asyncio
async def test_delegated_grandchild_of_self_heal_root_is_agent_authored(
    provenance_setup: dict,
) -> None:
    """Multi-hop walk (root -> child -> grandchild), and proves the
    self-heal landmine never fires: confirmed_by_human is never touched and
    the grandchild's source stays "manual"."""
    db = provenance_setup["db"]
    root = _task(
        provenance_setup["project_id"], provenance_setup["agent_id"], source="self_heal"
    )
    db.add(root)
    await db.flush()
    child = _task(
        provenance_setup["project_id"],
        provenance_setup["agent_id"],
        source="manual",
        parent_task_id=root.id,
    )
    db.add(child)
    await db.flush()
    grandchild = _task(
        provenance_setup["project_id"],
        provenance_setup["agent_id"],
        source="manual",
        parent_task_id=child.id,
    )
    db.add(grandchild)
    await db.flush()

    svc = provenance_setup["svc"]
    assert grandchild.source == "manual"
    assert await svc.resolve_root_source(grandchild.id) == "self_heal"
    assert await svc.is_human_authored(grandchild.id) is False


@pytest.mark.asyncio
async def test_nonexistent_task_resolves_none_and_not_human(
    provenance_setup: dict,
) -> None:
    svc = provenance_setup["svc"]
    missing = uuid4()
    assert await svc.resolve_root_source(missing) is None
    assert await svc.is_human_authored(missing) is False
