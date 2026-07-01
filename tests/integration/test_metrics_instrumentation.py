"""New audit instrumentation feeding the extra per-member metrics (phase 4).

- apply_escalation -> task.escalated (escalations metric)
- _unblock_dependents -> task.unblocked_dependents (blocked-others metric)
- mark_agent_idle -> agent.idle (idle/utilization metric)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, AuditLogTable, ProjectTable, TaskTable
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
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _agent(role: AgentRole, slug: str) -> AgentTable:
    return AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )


def _task(project_id: Any, created_by: Any, **over: Any) -> TaskTable:
    base: dict[str, Any] = {
        "id": uuid4(),
        "title": "t",
        "description": "d",
        "acceptance_criteria": ["ac"],
        "task_type": TaskType.CODE,
        "nature": TaskNature.TECHNICAL,
        "status": TaskStatus.IN_PROGRESS,
        "team": Team.BACKEND,
        "project_id": project_id,
        "created_by": created_by,
        "estimated_complexity": Complexity.MEDIUM,
    }
    base.update(over)
    return TaskTable(**base)


async def _audit_of(db: AsyncSession, event_type: str, target_id: Any) -> list[Any]:
    rows = (
        (
            await db.execute(
                select(AuditLogTable).where(
                    AuditLogTable.event_type == event_type,
                    AuditLogTable.target_id == target_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@pytest_asyncio.fixture
async def env(db_session: AsyncSession) -> AsyncIterator[dict]:
    dev = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")
    pm = _agent(AgentRole.CELL_PM, f"be-pm-{uuid4().hex[:6]}")
    db_session.add_all([dev, pm])
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=dev.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "db": db_session,
        "project_id": project.id,
        "dev": dev,
        "pm": pm,
    }


@pytest.mark.asyncio
async def test_apply_escalation_emits_task_escalated(env: dict) -> None:
    db = env["db"]
    task = _task(
        env["project_id"],
        env["dev"].id,
        assigned_to=env["dev"].id,
        claimed_by=env["dev"].id,
    )
    db.add(task)
    await db.flush()
    ok = await env["svc"].apply_escalation(
        task=task,
        target_agent_id=env["pm"].id,
        escalator_slug=env["dev"].slug,
        target_slug=env["pm"].slug,
        reason="need help with the seam",
    )
    assert ok is True
    rows = await _audit_of(db, "task.escalated", task.id)
    assert len(rows) == 1
    assert rows[0].details["escalator_slug"] == env["dev"].slug


@pytest.mark.asyncio
async def test_unblock_dependents_emits_count(env: dict) -> None:
    db = env["db"]
    blocker = _task(env["project_id"], env["dev"].id, status=TaskStatus.COMPLETED)
    db.add(blocker)
    await db.flush()
    dependent = _task(
        env["project_id"],
        env["dev"].id,
        status=TaskStatus.BLOCKED,
        dependency_ids=[blocker.id],
        assigned_to=env["dev"].id,
        claimed_by=env["dev"].id,
    )
    db.add(dependent)
    await db.flush()
    await env["svc"]._unblock_dependents(blocker.id)
    rows = await _audit_of(db, "task.unblocked_dependents", blocker.id)
    assert len(rows) == 1
    assert rows[0].details["count"] == 1


@pytest.mark.asyncio
async def test_mark_agent_idle_emits_agent_idle(env: dict) -> None:
    db = env["db"]
    await env["svc"].mark_agent_idle(env["dev"].id)
    rows = await _audit_of(db, "agent.idle", env["dev"].id)
    assert len(rows) == 1
    assert rows[0].details["agent_slug"] == env["dev"].slug
    refreshed = await db.get(AgentTable, env["dev"].id)
    assert refreshed is not None
    assert refreshed.status == AgentStatus.IDLE
