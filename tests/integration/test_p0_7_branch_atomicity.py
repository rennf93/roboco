"""P0-7 / S-01: branch creation atomicity.

When ``_ensure_branch_for_task`` raises (git checkout fails, push fails,
no token, etc.), ``_finalize_claim`` must roll back the claim fields it
just flushed — otherwise the task is left CLAIMED with branch_name=NULL
and the next claim attempt collides on a non-idempotent
``git checkout -b``.

This test exercises the rollback path against a real Postgres session
by patching ``_ensure_branch_for_task`` to raise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def claim_setup(db_session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
    system_agent = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="system",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(system_agent)
    await db_session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="Atom Test",
        slug=f"atom-{uuid4().hex[:8]}",
        git_url="https://github.com/example/atom.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_agent.id,
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()

    dev = AgentTable(
        id=uuid4(),
        name="BE Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=["python"],
        permissions={},
        metrics={},
    )
    db_session.add(dev)
    await db_session.flush()

    task = TaskTable(
        id=uuid4(),
        title="Task that will fail at branch creation",
        description="…",
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=project.id,
        created_by=system_agent.id,
        assigned_to=dev.id,
        acceptance_criteria=["does the thing"],
        # No branch_name — claim path will try to create one.
    )
    db_session.add(task)
    await db_session.flush()
    yield {"task": task, "dev": dev, "project": project}


@pytest.mark.asyncio
async def test_finalize_claim_rolls_back_on_branch_failure(
    db_session: AsyncSession, claim_setup: dict[str, Any]
) -> None:
    """git failure during _ensure_branch_for_task must revert claim fields.

    Without rollback the task is left CLAIMED with branch_name=NULL and
    `git checkout -b` is non-idempotent on retry.
    """
    task = claim_setup["task"]
    dev = claim_setup["dev"]
    svc = TaskService(db_session)

    # Snapshot pre-claim state so we can assert exact rollback.
    pre_status = task.status
    pre_assigned = task.assigned_to
    pre_claimed_by = task.claimed_by
    pre_claimed_at = task.claimed_at
    pre_heartbeat = task.last_heartbeat_at
    pre_claimant = task.active_claimant_id

    async def boom(_self: Any, _task: Any, _agent_id: Any) -> str:
        raise RuntimeError("simulated: git checkout -b failed")

    with (
        patch.object(TaskService, "_ensure_branch_for_task", boom),
        pytest.raises(RuntimeError, match="git checkout -b failed"),
    ):
        await svc.claim(task.id, dev.id)

    # Re-read the task from a clean state via a fresh fetch.
    refreshed = await svc.get(task.id)
    assert refreshed is not None
    assert refreshed.status == pre_status, "P0-7: status must roll back"
    assert refreshed.assigned_to == pre_assigned, "P0-7: assigned_to must roll back"
    assert refreshed.claimed_by == pre_claimed_by, "P0-7: claimed_by must roll back"
    assert refreshed.claimed_at == pre_claimed_at, "P0-7: claimed_at must roll back"
    assert refreshed.last_heartbeat_at == pre_heartbeat, (
        "P0-7: heartbeat must roll back"
    )
    assert refreshed.active_claimant_id == pre_claimant, (
        "P1-4 + P0-7: active_claimant_id must roll back too"
    )
