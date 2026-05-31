from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.product import router as product_router
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.permissions import AgentContext

_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


@pytest_asyncio.fixture
async def product_client(db_session):  # -> AsyncIterator[dict]
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="BE",
        slug=f"be-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=pm.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(product_router, prefix="/api/products")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=pm.id, role=AgentRole.MAIN_PM, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "project": project, "db": db_session}
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_and_get_product_with_cells(product_client: dict) -> None:
    slug = f"prod-{uuid4().hex[:6]}"
    create = await product_client["client"].post(
        "/api/products",
        json={
            "name": "RoboCo",
            "slug": slug,
            "cells": [
                {"team": "backend", "project_id": str(product_client["project"].id)}
            ],
        },
        headers=_HDR,
    )
    assert create.status_code == HTTPStatus.CREATED, create.text
    pid = create.json()["id"]

    got = await product_client["client"].get(f"/api/products/{pid}", headers=_HDR)
    assert got.status_code == HTTPStatus.OK
    body = got.json()
    assert body["slug"] == slug
    assert body["cells"] == [
        {"team": "backend", "project_id": str(product_client["project"].id)}
    ]


@pytest.mark.asyncio
async def test_create_product_duplicate_slug_returns_409(
    product_client: dict,
) -> None:
    slug = f"prod-{uuid4().hex[:6]}"
    first = await product_client["client"].post(
        "/api/products",
        json={"name": "RoboCo", "slug": slug, "cells": []},
        headers=_HDR,
    )
    assert first.status_code == HTTPStatus.CREATED, first.text

    second = await product_client["client"].post(
        "/api/products",
        json={"name": "Other", "slug": slug, "cells": []},
        headers=_HDR,
    )
    assert second.status_code == HTTPStatus.CONFLICT, second.text


@pytest.mark.asyncio
async def test_create_product_duplicate_team_cell_returns_409(
    product_client: dict,
) -> None:
    project_id = str(product_client["project"].id)
    create = await product_client["client"].post(
        "/api/products",
        json={
            "name": "RoboCo",
            "slug": f"prod-{uuid4().hex[:6]}",
            "cells": [
                {"team": "backend", "project_id": project_id},
                {"team": "backend", "project_id": project_id},
            ],
        },
        headers=_HDR,
    )
    assert create.status_code == HTTPStatus.CONFLICT, create.text


@pytest.mark.asyncio
async def test_create_product_unknown_project_cell_returns_422(
    product_client: dict,
) -> None:
    create = await product_client["client"].post(
        "/api/products",
        json={
            "name": "RoboCo",
            "slug": f"prod-{uuid4().hex[:6]}",
            "cells": [{"team": "backend", "project_id": str(uuid4())}],
        },
        headers=_HDR,
    )
    assert create.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, create.text


@pytest.mark.asyncio
async def test_update_product_duplicate_team_cell_returns_409(
    product_client: dict,
) -> None:
    slug = f"prod-{uuid4().hex[:6]}"
    create = await product_client["client"].post(
        "/api/products",
        json={"name": "RoboCo", "slug": slug, "cells": []},
        headers=_HDR,
    )
    assert create.status_code == HTTPStatus.CREATED, create.text
    pid = create.json()["id"]

    project_id = str(product_client["project"].id)
    patch = await product_client["client"].patch(
        f"/api/products/{pid}",
        json={
            "cells": [
                {"team": "backend", "project_id": project_id},
                {"team": "backend", "project_id": project_id},
            ]
        },
        headers=_HDR,
    )
    assert patch.status_code == HTTPStatus.CONFLICT, patch.text


@pytest.mark.asyncio
async def test_update_product_unknown_project_cell_returns_422(
    product_client: dict,
) -> None:
    slug = f"prod-{uuid4().hex[:6]}"
    create = await product_client["client"].post(
        "/api/products",
        json={"name": "RoboCo", "slug": slug, "cells": []},
        headers=_HDR,
    )
    assert create.status_code == HTTPStatus.CREATED, create.text
    pid = create.json()["id"]

    patch = await product_client["client"].patch(
        f"/api/products/{pid}",
        json={"cells": [{"team": "backend", "project_id": str(uuid4())}]},
        headers=_HDR,
    )
    assert patch.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, patch.text
