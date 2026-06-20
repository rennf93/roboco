from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation.identity import Team
from roboco.models import AgentRole, AgentStatus
from roboco.models.product import ProductCellMapping, ProductCreate, ProductUpdate
from roboco.services.base import ConflictError
from roboco.services.product import ProductService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def product_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
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
    projects = {}
    for cell in (Team.BACKEND, Team.FRONTEND, Team.UX_UI):
        p = ProjectTable(
            id=uuid4(),
            name=cell.value,
            slug=f"{cell.value}-{uuid4().hex[:6]}",
            git_url="https://example.com/r.git",
            assigned_cell=cell,
            created_by=system.id,
        )
        db_session.add(p)
        projects[cell] = p
    await db_session.flush()
    yield {
        "svc": ProductService(db_session),
        "creator": system.id,
        "projects": projects,
    }


@pytest.mark.asyncio
async def test_create_with_cells_and_project_for(product_setup: dict) -> None:
    svc = product_setup["svc"]
    projects = product_setup["projects"]
    product = await svc.create(
        ProductCreate(
            name="RoboCo",
            slug=f"roboco-{uuid4().hex[:6]}",
            cells=[
                ProductCellMapping(
                    team=Team.BACKEND, project_id=projects[Team.BACKEND].id
                ),
                ProductCellMapping(
                    team=Team.FRONTEND, project_id=projects[Team.FRONTEND].id
                ),
            ],
        ),
        created_by=product_setup["creator"],
    )
    assert await svc.project_for(product.id, Team.BACKEND) == projects[Team.BACKEND].id
    assert (
        await svc.project_for(product.id, Team.FRONTEND) == projects[Team.FRONTEND].id
    )
    # missing-team mapping -> None (graceful fallback happens in the caller)
    assert await svc.project_for(product.id, Team.UX_UI) is None


@pytest.mark.asyncio
async def test_shared_project_across_cells(product_setup: dict) -> None:
    """Monorepo: every cell maps to the same Project."""
    svc = product_setup["svc"]
    shared = product_setup["projects"][Team.BACKEND].id
    product = await svc.create(
        ProductCreate(
            name="Mono",
            slug=f"mono-{uuid4().hex[:6]}",
            cells=[
                ProductCellMapping(team=c, project_id=shared)
                for c in (Team.BACKEND, Team.FRONTEND, Team.UX_UI)
            ],
        ),
        created_by=product_setup["creator"],
    )
    for c in (Team.BACKEND, Team.FRONTEND, Team.UX_UI):
        assert await svc.project_for(product.id, c) == shared


@pytest.mark.asyncio
async def test_distinct_project_ids_monorepo_and_multirepo(product_setup: dict) -> None:
    """One integration branch per DISTINCT repo: monorepo => 1, multi-repo => N."""
    svc = product_setup["svc"]
    projects = product_setup["projects"]
    shared = projects[Team.BACKEND].id

    mono = await svc.create(
        ProductCreate(
            name="Mono",
            slug=f"mono-{uuid4().hex[:6]}",
            cells=[
                ProductCellMapping(team=c, project_id=shared)
                for c in (Team.BACKEND, Team.FRONTEND, Team.UX_UI)
            ],
        ),
        created_by=product_setup["creator"],
    )
    assert await svc.distinct_project_ids(mono.id) == [shared]

    multi = await svc.create(
        ProductCreate(
            name="Multi",
            slug=f"multi-{uuid4().hex[:6]}",
            cells=[
                ProductCellMapping(team=c, project_id=projects[c].id)
                for c in (Team.BACKEND, Team.FRONTEND, Team.UX_UI)
            ],
        ),
        created_by=product_setup["creator"],
    )
    assert set(await svc.distinct_project_ids(multi.id)) == {
        projects[Team.BACKEND].id,
        projects[Team.FRONTEND].id,
        projects[Team.UX_UI].id,
    }


@pytest.mark.asyncio
async def test_duplicate_slug_conflicts(product_setup: dict) -> None:
    svc = product_setup["svc"]
    slug = f"dup-{uuid4().hex[:6]}"
    await svc.create(
        ProductCreate(name="A", slug=slug), created_by=product_setup["creator"]
    )
    with pytest.raises(ConflictError):
        await svc.create(
            ProductCreate(name="B", slug=slug), created_by=product_setup["creator"]
        )


@pytest.mark.asyncio
async def test_update_remaps_existing_cells_without_unique_collision(
    product_setup: dict,
) -> None:
    """Re-mapping cells that already have a project must not collide.

    ``_replace_cells`` deletes the old (product_id, team) rows and inserts the
    new ones. Within a single flush SQLAlchemy orders INSERTs before DELETEs, so
    without flushing the deletes first the new rows hit
    ``uq_product_projects_product_team`` — the 409 seen when editing a product's
    projects. Regression for that ordering bug.
    """
    svc = product_setup["svc"]
    projects = product_setup["projects"]
    product = await svc.create(
        ProductCreate(
            name="Remap",
            slug=f"remap-{uuid4().hex[:6]}",
            cells=[
                ProductCellMapping(team=c, project_id=projects[c].id)
                for c in (Team.BACKEND, Team.FRONTEND, Team.UX_UI)
            ],
        ),
        created_by=product_setup["creator"],
    )
    # Every team already has a mapping; re-map all three to different projects.
    swapped = {
        Team.BACKEND: projects[Team.FRONTEND].id,
        Team.FRONTEND: projects[Team.UX_UI].id,
        Team.UX_UI: projects[Team.BACKEND].id,
    }
    updated = await svc.update(
        product.id,
        ProductUpdate(
            cells=[
                ProductCellMapping(team=t, project_id=pid) for t, pid in swapped.items()
            ]
        ),
    )
    assert updated is not None
    for team, pid in swapped.items():
        assert await svc.project_for(product.id, team) == pid


@pytest.mark.asyncio
async def test_update_replaces_cells(product_setup: dict) -> None:
    svc = product_setup["svc"]
    projects = product_setup["projects"]
    product = await svc.create(
        ProductCreate(
            name="U",
            slug=f"u-{uuid4().hex[:6]}",
            cells=[
                ProductCellMapping(
                    team=Team.BACKEND, project_id=projects[Team.BACKEND].id
                )
            ],
        ),
        created_by=product_setup["creator"],
    )
    await svc.update(
        product.id,
        ProductUpdate(
            cells=[
                ProductCellMapping(team=Team.UX_UI, project_id=projects[Team.UX_UI].id)
            ]
        ),
    )
    assert await svc.project_for(product.id, Team.BACKEND) is None
    assert await svc.project_for(product.id, Team.UX_UI) == projects[Team.UX_UI].id
