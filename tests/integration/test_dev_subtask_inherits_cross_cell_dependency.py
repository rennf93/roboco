"""A dev/code subtask under a frontend cell task that is waiting on the UX/UI
design must inherit the unresolved cross-cell dependency, so the developer is
held until UX is done instead of coding ahead of the design.

The frontend CELL task already waits on the UX/UI cell task (cross-cell
sequencing). When the cell PM delegates a dev subtask under that cell task, the
subtask must NOT become dispatchable while the UX dependency is unresolved, and
must become dispatchable once the UX task reaches a terminal state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProductTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.task import TaskCreateRequest
from roboco.services.gateway.choreographer._impl import (
    Choreographer,
    ChoreographerDeps,
)
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def fanout_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
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
    be_project = ProjectTable(
        id=uuid4(),
        name="BE",
        slug=f"be-{uuid4().hex[:6]}",
        git_url="https://example.com/be.git",
        assigned_cell=Team.BACKEND,
        created_by=system.id,
    )
    product = ProductTable(
        id=uuid4(),
        name="Prod",
        slug=f"prod-{uuid4().hex[:6]}",
        created_by=system.id,
    )
    db_session.add_all([fe_project, ux_project, be_project, product])
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
    yield {
        "svc": svc,
        "choreo": choreo,
        "creator": system.id,
        "fe_dev_id": fe_dev.id,
        "fe_project_id": fe_project.id,
        "ux_project_id": ux_project.id,
        "be_project_id": be_project.id,
        "product_id": product.id,
    }


async def _build_product_fanout(setup: dict) -> dict:
    """Product root with FE + UX cell tasks as siblings, FE waiting on UX."""
    svc: TaskService = setup["svc"]
    choreo: Choreographer = setup["choreo"]

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
    # Cross-cell sequencing: wire the frontend CELL task onto the UX cell task
    # exactly as the product fan-out does on delegate.
    await choreo._wire_ux_frontend_dependency(fe_cell, root)
    await svc.session.flush()
    refreshed_fe = await svc.get(fe_cell.id)
    assert refreshed_fe is not None
    assert ux_cell.id in refreshed_fe.dependency_ids, (
        "precondition: frontend cell task must depend on the UX cell task"
    )
    return {"root": root, "ux_cell": ux_cell, "fe_cell": fe_cell}


@pytest.mark.asyncio
async def test_dev_subtask_held_until_ux_dependency_resolves(
    fanout_setup: dict,
) -> None:
    svc: TaskService = fanout_setup["svc"]
    choreo: Choreographer = fanout_setup["choreo"]
    tree = await _build_product_fanout(fanout_setup)
    fe_cell = tree["fe_cell"]
    ux_cell = tree["ux_cell"]

    # Cell PM delegates a dev/code subtask under the frontend cell task.
    dev_subtask = await svc.create_subtask(
        TaskCreateRequest(
            title="Implement the login form component",
            description="a real dev subtask description over twenty chars long",
            acceptance_criteria=["form renders and submits"],
            team=Team.FRONTEND,
            created_by=fanout_setup["creator"],
            project_id=fanout_setup["fe_project_id"],
            product_id=fanout_setup["product_id"],
            parent_task_id=fe_cell.id,
            assigned_to=fanout_setup["fe_dev_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    assert dev_subtask.status == TaskStatus.PENDING
    # The delegate flow wires cross-cell sequencing on the new subtask.
    await choreo._wire_ux_frontend_dependency(dev_subtask, fe_cell)
    await svc.session.flush()

    # While the UX cell task is unresolved, the dev subtask must NOT be
    # dispatchable (held by the dependency filter).
    pending = await svc.list_pending(team=Team.FRONTEND, filter_by_dependencies=True)
    pending_ids = {t.id for t in pending}
    assert dev_subtask.id not in pending_ids, (
        "dev subtask must be held while the UX dependency is unresolved"
    )

    # UX finishes -> terminal state.
    ux_row = await svc.get(ux_cell.id)
    assert ux_row is not None
    ux_row.status = TaskStatus.COMPLETED
    await svc.session.flush()

    # Now the dev subtask is dispatchable.
    pending_after = await svc.list_pending(
        team=Team.FRONTEND, filter_by_dependencies=True
    )
    pending_after_ids = {t.id for t in pending_after}
    assert dev_subtask.id in pending_after_ids, (
        "dev subtask must become dispatchable once UX reaches a terminal state"
    )


@pytest.mark.asyncio
async def test_dependent_cell_sequence_follows_upstream_ux(
    fanout_setup: dict,
) -> None:
    """The frontend cell task sorts after its UX upstream: wiring the
    dependency also bumps its sequence to the UX task's sequence + 1, so
    list ordering and the panel show UX before the work it gates."""
    svc: TaskService = fanout_setup["svc"]
    tree = await _build_product_fanout(fanout_setup)
    ux_row = await svc.get(tree["ux_cell"].id)
    fe_row = await svc.get(tree["fe_cell"].id)
    assert ux_row is not None and fe_row is not None
    assert fe_row.sequence == (ux_row.sequence or 0) + 1, (
        "the dependent frontend task must sort one step after its UX upstream"
    )


@pytest.mark.asyncio
async def test_backend_cell_also_depends_on_ux(fanout_setup: dict) -> None:
    """UX/UI design defines the API contracts the backend builds against, so a
    backend cell task in the same fan-out also waits on the UX cell task and
    sorts after it."""
    svc: TaskService = fanout_setup["svc"]
    choreo: Choreographer = fanout_setup["choreo"]
    tree = await _build_product_fanout(fanout_setup)
    root = tree["root"]
    ux_cell = tree["ux_cell"]

    be_cell = await svc.create_subtask(
        TaskCreateRequest(
            title="Backend implementation for the feature",
            description="a real backend cell task description over twenty chars",
            acceptance_criteria=["endpoints satisfy the contract"],
            team=Team.BACKEND,
            created_by=fanout_setup["creator"],
            project_id=fanout_setup["be_project_id"],
            product_id=fanout_setup["product_id"],
            parent_task_id=root.id,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    # Forward order: the backend cell is delegated after the UX cell exists.
    await choreo._wire_ux_frontend_dependency(be_cell, root)
    await svc.session.flush()

    be_row = await svc.get(be_cell.id)
    ux_row = await svc.get(ux_cell.id)
    assert be_row is not None and ux_row is not None
    assert ux_cell.id in be_row.dependency_ids, (
        "backend cell task must depend on the UX cell task"
    )
    assert be_row.sequence == (ux_row.sequence or 0) + 1, (
        "the backend task must sort one step after its UX upstream"
    )


@pytest.mark.asyncio
async def test_pending_impl_cells_retrowired_when_ux_arrives_later(
    fanout_setup: dict,
) -> None:
    """When the UX cell task is delegated AFTER still-pending frontend and
    backend siblings, both are retro-wired onto UX and sorted after it — the
    'either delegation order' guarantee, for both implementation cells."""
    svc: TaskService = fanout_setup["svc"]
    choreo: Choreographer = fanout_setup["choreo"]

    root = await svc.create(
        TaskCreateRequest(
            title="Build the feature (board fan-out)",
            description="a real coordination task description over twenty chars",
            acceptance_criteria=["delegated to frontend + backend + ux_ui cells"],
            team=Team.BOARD,
            created_by=fanout_setup["creator"],
            project_id=None,
            product_id=fanout_setup["product_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.NON_TECHNICAL,
            estimated_complexity=Complexity.HIGH,
        )
    )
    fe_cell = await svc.create_subtask(
        TaskCreateRequest(
            title="Frontend implementation for the feature",
            description="a real frontend cell task description over twenty chars",
            acceptance_criteria=["UI matches the design"],
            team=Team.FRONTEND,
            created_by=fanout_setup["creator"],
            project_id=fanout_setup["fe_project_id"],
            product_id=fanout_setup["product_id"],
            parent_task_id=root.id,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    be_cell = await svc.create_subtask(
        TaskCreateRequest(
            title="Backend implementation for the feature",
            description="a real backend cell task description over twenty chars",
            acceptance_criteria=["endpoints satisfy the contract"],
            team=Team.BACKEND,
            created_by=fanout_setup["creator"],
            project_id=fanout_setup["be_project_id"],
            product_id=fanout_setup["product_id"],
            parent_task_id=root.id,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    # UX is delegated LAST — both pending implementation cells must be wired.
    ux_cell = await svc.create_subtask(
        TaskCreateRequest(
            title="UX/UI design for the feature",
            description="a real ux design task description over twenty chars",
            acceptance_criteria=["wireframes approved"],
            team=Team.UX_UI,
            created_by=fanout_setup["creator"],
            project_id=fanout_setup["ux_project_id"],
            product_id=fanout_setup["product_id"],
            parent_task_id=root.id,
            task_type=TaskType.DESIGN,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    await choreo._wire_ux_frontend_dependency(ux_cell, root)
    await svc.session.flush()

    fe_row = await svc.get(fe_cell.id)
    be_row = await svc.get(be_cell.id)
    ux_row = await svc.get(ux_cell.id)
    assert fe_row is not None and be_row is not None and ux_row is not None
    assert ux_cell.id in fe_row.dependency_ids, "frontend must retro-wire onto UX"
    assert ux_cell.id in be_row.dependency_ids, "backend must retro-wire onto UX"
    expected_sequence = (ux_row.sequence or 0) + 1
    assert fe_row.sequence == expected_sequence
    assert be_row.sequence == expected_sequence
