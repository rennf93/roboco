"""A pre-assigned dev subtask with an unmet (non-terminal) dependency must be
held at EVERY path the developer actually arrives by — not only by the
unassigned claim pool's dependency filter.

A dev subtask is always pre-assigned (``assigned_to=<dev>``), so it never flows
through ``list_pending(filter_by_dependencies=True)``. The three real arrival
paths are exercised here against a real database:

  (a) orchestrator spawn dispatch — ``_validate_task_for_spawn`` (the HTTP path
      the dev container is spawned by) must return a skip reason and not spawn;
  (b) ``give_me_work`` — ``TaskService.list_pending_for_agent`` must exclude it;
  (c) claim — the Choreographer's ``_run_claim_guards`` (invoked by the
      ``i_will_work_on`` verb) must reject it.

Once the UX/UI dependency reaches a terminal state, all three allow the dev to
proceed. This intentionally does NOT assert via
``list_pending(filter_by_dependencies=True)`` — that gate serves the
unassigned claim pool, not the pre-assigned dev.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import PropertyMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_db
from roboco.api.routes.tasks import router as tasks_router
from roboco.db.tables import AgentTable, ProductTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.task import TaskCreateRequest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.gateway.choreographer._impl import (
    Choreographer,
    ChoreographerDeps,
)
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

# A canonical developer slug so the orchestrator's get_agent_role() classifies
# the assignee as "developer" and the real dev-spawn validation runs.
_DEV_SLUG = "fe-dev-1"
_API_BASE = "http://test/api"


@pytest_asyncio.fixture
async def dep_gate_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
    system = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="s",
        capabilities=[],
        permissions={},
        metrics={},
    )
    fe_dev = AgentTable(
        id=uuid4(),
        name="FE Dev",
        slug=f"fe-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.FRONTEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="s",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([system, fe_dev])
    await db_session.flush()
    fe_project = ProjectTable(
        id=uuid4(),
        name="FE",
        slug=f"fe-{uuid4().hex[:6]}",
        git_url="https://example.com/fe.git",
        assigned_cell=Team.FRONTEND,
        created_by=system.id,
    )
    ux_project = ProjectTable(
        id=uuid4(),
        name="UX",
        slug=f"ux-{uuid4().hex[:6]}",
        git_url="https://example.com/ux.git",
        assigned_cell=Team.UX_UI,
        created_by=system.id,
    )
    product = ProductTable(
        id=uuid4(),
        name="Prod",
        slug=f"prod-{uuid4().hex[:6]}",
        created_by=system.id,
    )
    db_session.add_all([fe_project, ux_project, product])
    await db_session.flush()

    svc = TaskService(db_session)
    choreo = Choreographer(
        ChoreographerDeps(
            task=svc,
            work_session=None,
            git=None,
            a2a=None,
            journal=None,
            audit=None,
            evidence_repo=None,
        )
    )

    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/tasks")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_db

    # Bare orchestrator (skip __init__/settings I/O); _api_url is patched to the
    # in-process ASGI app so its HTTP dispatch hits the real DB.
    orch = AgentOrchestrator.__new__(AgentOrchestrator)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(
            AgentOrchestrator,
            "_api_url",
            new_callable=PropertyMock,
            return_value=_API_BASE,
        ):
            yield {
                "svc": svc,
                "choreo": choreo,
                "orch": orch,
                "client": client,
                "creator": system.id,
                "fe_dev_db_id": fe_dev.id,
                "fe_project_id": fe_project.id,
                "ux_project_id": ux_project.id,
                "product_id": product.id,
            }
    app.dependency_overrides.clear()


async def _seed_dev_subtask_with_unmet_dep(setup: dict) -> dict:
    """A frontend dev subtask pre-assigned to the dev, depending on a UX task.

    Mirrors the product fan-out: a UX/UI cell task and a frontend cell task
    under a board root, with a pre-assigned dev subtask under the frontend cell
    whose dependency is the (still pending) UX task.
    """
    svc: TaskService = setup["svc"]
    root = await svc.create(
        TaskCreateRequest(
            title="Build the feature (board fan-out)",
            description="a real coordination task description over twenty chars",
            acceptance_criteria=["delegated to frontend + ux_ui cells"],
            team=Team.BOARD,
            created_by=setup["creator"],
            project_id=None,
            product_id=setup["product_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.NON_TECHNICAL,
            estimated_complexity=Complexity.HIGH,
        )
    )
    ux_cell = await svc.create_subtask(
        TaskCreateRequest(
            title="UX/UI design for the feature",
            description="a real ux design task description over twenty chars",
            acceptance_criteria=["wireframes approved"],
            team=Team.UX_UI,
            created_by=setup["creator"],
            project_id=setup["ux_project_id"],
            product_id=setup["product_id"],
            parent_task_id=root.id,
            task_type=TaskType.DESIGN,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    fe_cell = await svc.create_subtask(
        TaskCreateRequest(
            title="Frontend implementation for the feature",
            description="a real frontend cell task description over twenty chars",
            acceptance_criteria=["UI matches the design"],
            team=Team.FRONTEND,
            created_by=setup["creator"],
            project_id=setup["fe_project_id"],
            product_id=setup["product_id"],
            parent_task_id=root.id,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    # Give the frontend cell (the dev subtask's parent) a branch so the
    # orchestrator's parent-branch gate is satisfied and the dependency gate is
    # the only thing that can hold the dev.
    fe_cell.branch_name = "feature/frontend/FECELL01"
    await svc.session.flush()

    dev_subtask = await svc.create_subtask(
        TaskCreateRequest(
            title="Implement the login form component",
            description="a real dev subtask description over twenty chars long",
            acceptance_criteria=["form renders and submits"],
            team=Team.FRONTEND,
            created_by=setup["creator"],
            project_id=setup["fe_project_id"],
            product_id=setup["product_id"],
            parent_task_id=fe_cell.id,
            assigned_to=setup["fe_dev_db_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    assert dev_subtask.status == TaskStatus.PENDING
    # The dev subtask depends on the UX cell task (cross-cell sequencing).
    await svc.add_dependency(dev_subtask.id, ux_cell.id)
    await svc.session.flush()
    refreshed = await svc.get(dev_subtask.id)
    assert refreshed is not None
    assert ux_cell.id in refreshed.dependency_ids, (
        "precondition: dev subtask must depend on the UX cell task"
    )
    return {"ux_cell": ux_cell, "fe_cell": fe_cell, "dev_subtask": dev_subtask}


def _as_dict(task: TaskTable) -> dict:
    """The task dict the orchestrator dispatcher operates on."""
    return {
        "id": str(task.id),
        "description": task.description,
        "project_id": str(task.project_id) if task.project_id else None,
        "product_id": str(task.product_id) if task.product_id else None,
        "parent_task_id": str(task.parent_task_id) if task.parent_task_id else None,
        "dependency_ids": [str(d) for d in task.dependency_ids],
        "estimated_complexity": task.estimated_complexity.value,
        "task_type": task.task_type.value,
    }


@pytest.mark.asyncio
async def test_all_three_dev_paths_gate_then_release(dep_gate_setup: dict) -> None:
    svc: TaskService = dep_gate_setup["svc"]
    choreo: Choreographer = dep_gate_setup["choreo"]
    orch: AgentOrchestrator = dep_gate_setup["orch"]
    client: AsyncClient = dep_gate_setup["client"]
    fe_dev_db_id = dep_gate_setup["fe_dev_db_id"]

    tree = await _seed_dev_subtask_with_unmet_dep(dep_gate_setup)
    ux_cell = tree["ux_cell"]
    dev_subtask = tree["dev_subtask"]
    dev_dict = _as_dict(dev_subtask)

    # --- (a) orchestrator spawn dispatch: _validate_task_for_spawn ---
    # REAL boundary: the dev container is spawned by _spawn_pending_dev ->
    # _validate_task_for_spawn. With UX pending, it must return a skip reason.
    issue = await orch._validate_task_for_spawn(client, dev_dict, _DEV_SLUG)
    assert issue is not None, "spawn validation must hold the dev while UX is unmet"
    assert "dependency" in issue

    # --- (b) give_me_work has TWO offer paths and both must exclude the held
    #     subtask: list_pending_for_agent (primary) and the
    #     list_assigned_for_agent fallback (PENDING rows, no built-in dep filter).
    offered = await svc.list_pending_for_agent(fe_dev_db_id)
    assert dev_subtask.id not in {t.id for t in offered}, (
        "list_pending_for_agent must not offer the held dev subtask"
    )
    assigned = await svc.list_assigned_for_agent(fe_dev_db_id)
    assert dev_subtask.id in {t.id for t in assigned}, (
        "sanity: the held subtask is assigned+pending, so the fallback path is real"
    )
    offerable = await choreo._drop_dependency_held(assigned)
    assert dev_subtask.id not in {t.id for t in offerable}, (
        "give_me_work's list_assigned_for_agent fallback must not offer it"
    )

    # --- (c) claim: _run_claim_guards (the i_will_work_on guard set) ---
    held = await svc.get(dev_subtask.id)
    guard = await choreo._run_claim_guards(agent_id=fe_dev_db_id, task=held)
    assert guard is not None, "claim guard must reject while UX is unmet"
    assert guard.error == "invalid_state"
    assert guard.remediate is not None
    assert str(ux_cell.id) in guard.remediate

    # --- UX dependency reaches a terminal state ---
    ux_row = await svc.get(ux_cell.id)
    assert ux_row is not None
    ux_row.status = TaskStatus.COMPLETED
    await svc.session.flush()

    # All three now allow the dev to proceed.
    assert await orch._validate_task_for_spawn(client, dev_dict, _DEV_SLUG) is None, (
        "spawn validation must allow the dev once UX is terminal"
    )
    offered_after = await svc.list_pending_for_agent(fe_dev_db_id)
    assert dev_subtask.id in {t.id for t in offered_after}, (
        "list_pending_for_agent must offer the dev subtask once UX is terminal"
    )
    offerable_after = await choreo._drop_dependency_held(
        await svc.list_assigned_for_agent(fe_dev_db_id)
    )
    assert dev_subtask.id in {t.id for t in offerable_after}, (
        "the assigned-fallback gate must release the subtask once UX is terminal"
    )
    released = await svc.get(dev_subtask.id)
    assert (
        await choreo._run_claim_guards(agent_id=fe_dev_db_id, task=released) is None
    ), "claim guard must allow once UX is terminal"


@pytest.mark.asyncio
async def test_claimed_dependency_blocked_task_is_released_to_pending(
    dep_gate_setup: dict,
) -> None:
    """A CLAIMED task whose dependency is unmet is released back to pending.

    Unlike the pre-assigned-but-pending dev subtask, a cell task can reach
    CLAIMED with an unfinished dependency (the PM claims it before the upstream
    resolves). Left claimed, the orchestrator's respawn loop churns its
    assignee. The claim guard now releases it to pending — ``claimed -> blocked``
    is not a legal transition, so pending (held by the dependency filter) is the
    lifecycle-correct resting state, and ``_unblock_dependents`` re-dispatches it
    once the upstream completes.
    """
    svc: TaskService = dep_gate_setup["svc"]
    choreo: Choreographer = dep_gate_setup["choreo"]
    fe_dev_db_id = dep_gate_setup["fe_dev_db_id"]

    tree = await _seed_dev_subtask_with_unmet_dep(dep_gate_setup)
    dev_subtask = tree["dev_subtask"]

    # Force the held task to CLAIMED (the state a respawn loop churns on).
    dev_subtask.status = TaskStatus.CLAIMED
    dev_subtask.branch_name = "feature/frontend/DEVLEAF01"
    await svc.session.flush()

    held = await svc.get(dev_subtask.id)
    guard = await choreo._run_claim_guards(agent_id=fe_dev_db_id, task=held)
    assert guard is not None, "claim guard must still reject while UX is unmet"
    assert guard.error == "invalid_state"

    after = await svc.get(dev_subtask.id)
    assert after is not None
    assert after.status == TaskStatus.PENDING, (
        "a claimed dependency-blocked task must be released to pending"
    )
    assert after.assigned_to is None, "release clears the assignee"
    assert after.branch_name is None, (
        "release clears branch_name so the re-claim cuts fresh off the current "
        "integration tip (which by then includes the upstream's work)"
    )
