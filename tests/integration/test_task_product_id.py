from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
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
