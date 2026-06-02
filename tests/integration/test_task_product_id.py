from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.api.schemas.tasks import task_to_response
from roboco.db.tables import AgentTable, ProductTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.task import TaskCreateRequest
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def svc_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
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
    db_session.add(system)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=system.id,
    )
    product = ProductTable(
        id=uuid4(),
        name="Prod",
        slug=f"prod-{uuid4().hex[:6]}",
        created_by=system.id,
    )
    db_session.add_all([project, product])
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "creator": system.id,
        "project_id": project.id,
        "product_id": product.id,
    }


def _req(setup: dict, **kw) -> TaskCreateRequest:
    return TaskCreateRequest(
        title="t",
        description="a real description over twenty chars",
        acceptance_criteria=["GET /x returns 200"],
        team=Team.BACKEND,
        created_by=setup["creator"],
        project_id=setup["project_id"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        **kw,
    )


@pytest.mark.asyncio
async def test_create_threads_product_id(svc_setup: dict) -> None:
    task = await svc_setup["svc"].create(
        _req(svc_setup, product_id=svc_setup["product_id"])
    )
    assert task.product_id == svc_setup["product_id"]


@pytest.mark.asyncio
async def test_product_id_defaults_null(svc_setup: dict) -> None:
    task = await svc_setup["svc"].create(_req(svc_setup))
    assert task.product_id is None


@pytest.mark.asyncio
async def test_create_subtask_preserves_product_id(svc_setup: dict) -> None:
    parent = await svc_setup["svc"].create(
        _req(svc_setup, product_id=svc_setup["product_id"])
    )
    sub = await svc_setup["svc"].create_subtask(
        _req(
            svc_setup,
            product_id=svc_setup["product_id"],
            parent_task_id=parent.id,
            assigned_to=svc_setup["creator"],
        )
    )
    assert sub.product_id == svc_setup["product_id"]


@pytest.mark.asyncio
async def test_response_surfaces_product_id(svc_setup: dict) -> None:
    with_product = await svc_setup["svc"].create(
        _req(svc_setup, product_id=svc_setup["product_id"])
    )
    without = await svc_setup["svc"].create(_req(svc_setup))
    # product_id is a scalar column (loaded after flush) — task_to_response maps
    # it via to_python_uuid without triggering the project_slug lazy-load guard.
    assert task_to_response(with_product).product_id == svc_setup["product_id"]
    assert task_to_response(without).product_id is None


@pytest.mark.asyncio
async def test_coordination_task_claims_plans_and_starts_without_branch(
    svc_setup: dict, db_session: AsyncSession
) -> None:
    """End-to-end: a project-less coordination task (product set, no repo) can be
    claimed, planned, and STARTED — reaching in_progress without a branch.

    This is the regression that killed the board->cells fan-out: a coordination
    task could be created and claimed, but start()'s claimed->in_progress
    transition hit validate_git_requirements, which demanded a branch_name and
    raised GitRequirementError. So Main PM's i_will_plan never completed, it
    looped, and the task never delegated. A coordination task does no git of its
    own, so it must transition to in_progress branchless. This exercises exactly
    the claim->set_plan->start sequence the gateway's i_will_plan composes.
    """
    svc: TaskService = svc_setup["svc"]
    pm = AgentTable(
        id=uuid4(),
        name="Main PM",
        slug=f"main-pm-{uuid4().hex[:6]}",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="s",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()

    task = await svc.create(
        TaskCreateRequest(
            title="Prompter (board fan-out)",
            description="a real coordination task description over twenty chars",
            acceptance_criteria=["delegated to backend, frontend, ux_ui cells"],
            team=Team.BOARD,
            created_by=svc_setup["creator"],
            project_id=None,
            product_id=svc_setup["product_id"],
            assigned_to=pm.id,
            task_type=TaskType.CODE,
            nature=TaskNature.NON_TECHNICAL,
            estimated_complexity=Complexity.HIGH,
        )
    )
    assert task.project_id is None
    assert task.product_id == svc_setup["product_id"]

    claimed = await svc.claim(task.id, pm.id)
    assert claimed is not None, "coordination task could not be claimed"
    await svc.set_plan(
        task.id,
        {
            "approach": "delegate to the three cells",
            "sub_tasks": [{"title": "backend"}],
        },
    )

    # The fix: claimed->in_progress no longer requires a branch for a coordination
    # task, so start() succeeds instead of raising GitRequirementError.
    started = await svc.start(task.id, pm.id, agent_role=None)
    assert started is not None, (
        "start() returned None — coordination task failed to reach in_progress"
    )

    refreshed = await svc.get(task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.IN_PROGRESS
    assert not refreshed.branch_name  # coordination task does no git, has no branch
