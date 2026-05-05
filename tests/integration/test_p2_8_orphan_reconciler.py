"""P2-8: startup orphan-claim reconciler.

The orchestrator's `_reconcile_orphan_claims_on_startup` rolls back
tasks left in CLAIMED/IN_PROGRESS with `branch_name IS NULL` — the
half-state from a pre-P0-7 crash where `_finalize_claim` flushed
status=CLAIMED before branch creation failed.
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
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def orphan_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict[str, Any]]:
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
        name="Reconciler Test",
        slug=f"recon-{uuid4().hex[:8]}",
        git_url="https://github.com/example/recon.git",
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

    # ORPHAN: status=CLAIMED, assigned_to=dev, branch_name=NULL.
    orphan = TaskTable(
        id=uuid4(),
        title="Orphan from prior crash",
        description="…",
        status=TaskStatus.CLAIMED,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=project.id,
        created_by=system_agent.id,
        assigned_to=dev.id,
        claimed_by=dev.id,
        acceptance_criteria=["…"],
    )
    # HEALTHY: status=CLAIMED with branch — must NOT be rolled back.
    healthy = TaskTable(
        id=uuid4(),
        title="Healthy claim",
        description="…",
        status=TaskStatus.CLAIMED,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=project.id,
        created_by=system_agent.id,
        assigned_to=dev.id,
        claimed_by=dev.id,
        branch_name="feature/backend/healthy",
        acceptance_criteria=["…"],
    )
    db_session.add_all([orphan, healthy])
    await db_session.flush()
    yield {"orphan": orphan, "healthy": healthy, "dev": dev}


@pytest.mark.asyncio
async def test_reconciler_rolls_back_orphan_claims(
    db_session: AsyncSession, orphan_setup: dict[str, Any]
) -> None:
    """CLAIMED task with branch_name=NULL → reconciled to PENDING."""
    orphan = orphan_setup["orphan"]
    healthy = orphan_setup["healthy"]
    svc = TaskService(db_session)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)

    # Drive the logic directly via the test-injectable helper so we avoid
    # the orchestrator's session-factory dance.
    await orch._reconcile_with_service(svc)

    refreshed_orphan = await svc.get(orphan.id)
    refreshed_healthy = await svc.get(healthy.id)

    assert refreshed_orphan is not None
    assert str(refreshed_orphan.status) == "pending", (
        "P2-8: orphan must be rolled back to pending"
    )
    assert refreshed_orphan.assigned_to is None
    assert refreshed_orphan.active_claimant_id is None

    # Healthy claim untouched.
    assert refreshed_healthy is not None
    assert str(refreshed_healthy.status) == "claimed"
    assert refreshed_healthy.branch_name == "feature/backend/healthy"
