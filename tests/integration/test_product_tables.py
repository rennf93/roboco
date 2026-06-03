from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    ProductProjectTable,
    ProductTable,
    ProjectTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def product_table_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
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
    db_session.add(project)
    await db_session.flush()
    yield {"creator_id": system.id, "project_id": project.id}


@pytest.mark.asyncio
async def test_product_and_mapping_persist(
    product_table_setup: dict, db_session: AsyncSession
) -> None:
    product = ProductTable(
        id=uuid4(),
        name="RoboCo",
        slug=f"roboco-{uuid4().hex[:6]}",
        created_by=product_table_setup["creator_id"],
    )
    db_session.add(product)
    await db_session.flush()
    mapping = ProductProjectTable(
        id=uuid4(),
        product_id=product.id,
        team=Team.BACKEND,
        project_id=product_table_setup["project_id"],
    )
    db_session.add(mapping)
    await db_session.flush()

    row = (
        await db_session.execute(
            select(ProductProjectTable).where(
                ProductProjectTable.product_id == product.id
            )
        )
    ).scalar_one()
    assert row.team == Team.BACKEND
    assert row.project_id == product_table_setup["project_id"]
