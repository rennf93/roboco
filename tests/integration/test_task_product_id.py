from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.api.schemas.tasks import task_to_response
from roboco.db.tables import AgentTable, ProductTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskType
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
