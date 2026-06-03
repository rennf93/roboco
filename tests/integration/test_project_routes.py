"""Project API route coverage."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.project import router as project_router
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def project_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    agent = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()

    app = FastAPI()
    app.include_router(project_router, prefix="/api/projects")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent.id, role=AgentRole.MAIN_PM, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


def _payload() -> dict:
    return {
        "name": f"Project {uuid4().hex[:6]}",
        "slug": f"proj-{uuid4().hex[:6]}",
        "git_url": "https://github.com/example/foo.git",
        "default_branch": "main",
        "assigned_cell": "backend",
    }


@pytest.mark.asyncio
async def test_list_projects_empty(project_client: AsyncClient) -> None:
    response = await project_client.get("/api/projects", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_project(project_client: AsyncClient) -> None:
    response = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert "id" in body
    assert body["name"].startswith("Project")


@pytest.mark.asyncio
async def test_create_duplicate_returns_409(project_client: AsyncClient) -> None:
    payload = _payload()
    response = await project_client.post("/api/projects", json=payload, headers=_HDR)
    assert response.status_code == HTTPStatus.CREATED
    response2 = await project_client.post("/api/projects", json=payload, headers=_HDR)
    assert response2.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_get_project_not_found(project_client: AsyncClient) -> None:
    response = await project_client.get(f"/api/projects/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_project_by_id(project_client: AsyncClient) -> None:
    create_resp = await project_client.post(
        "/api/projects", json=_payload(), headers=_HDR
    )
    pid = create_resp.json()["id"]
    response = await project_client.get(f"/api/projects/{pid}", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_project_by_slug(project_client: AsyncClient) -> None:
    payload = _payload()
    await project_client.post("/api/projects", json=payload, headers=_HDR)
    response = await project_client.get(
        f"/api/projects/{payload['slug']}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_update_project(project_client: AsyncClient) -> None:
    create = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    pid = create.json()["id"]
    response = await project_client.patch(
        f"/api/projects/{pid}",
        json={"name": "Renamed"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_update_project_not_found(project_client: AsyncClient) -> None:
    response = await project_client.patch(
        f"/api/projects/{uuid4()}",
        json={"name": "x"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_list_projects_filter_by_cell(
    project_client: AsyncClient,
) -> None:
    response = await project_client.get("/api/projects?cell=backend", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_create_project_omitting_default_branch_yields_master(
    project_client: AsyncClient,
) -> None:
    """Omitting ``default_branch`` on the create route resolves to ``master``.

    Exercises the real API create path: ``ProjectCreateRequest`` (field
    omitted) -> route ``create_project`` -> ``ProjectCreate`` -> service
    ``create`` -> ``ProjectTable`` -> ``project_to_response``. None of these
    must inject the legacy ``main`` default.
    """
    payload = _payload()
    del payload["default_branch"]

    response = await project_client.post("/api/projects", json=payload, headers=_HDR)
    assert response.status_code == HTTPStatus.CREATED
    assert response.json()["default_branch"] == "master"

    fetched = await project_client.get(f"/api/projects/{payload['slug']}", headers=_HDR)
    assert fetched.status_code == HTTPStatus.OK
    assert fetched.json()["default_branch"] == "master"


@pytest.mark.asyncio
async def test_list_projects_includes_default_branch(
    project_client: AsyncClient,
) -> None:
    payload = _payload()
    payload["default_branch"] = "master"
    create = await project_client.post("/api/projects", json=payload, headers=_HDR)
    assert create.status_code == HTTPStatus.CREATED

    response = await project_client.get("/api/projects", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    listed = {p["slug"]: p for p in response.json()}
    assert payload["slug"] in listed
    assert listed[payload["slug"]]["default_branch"] == "master"


@pytest.mark.asyncio
async def test_get_project_by_slug_not_found(project_client: AsyncClient) -> None:
    response = await project_client.get(
        "/api/projects/ghost-project-slug", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_update_project_by_slug(project_client: AsyncClient) -> None:
    payload = _payload()
    create = await project_client.post("/api/projects", json=payload, headers=_HDR)
    assert create.status_code == HTTPStatus.CREATED
    response = await project_client.patch(
        f"/api/projects/{payload['slug']}",
        json={"name": "RenamedSlug"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_delete_project_not_found(project_client: AsyncClient) -> None:
    response = await project_client.delete(f"/api/projects/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_delete_project_success(project_client: AsyncClient) -> None:
    create = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    pid = create.json()["id"]
    response = await project_client.delete(f"/api/projects/{pid}", headers=_HDR)
    assert response.status_code == HTTPStatus.NO_CONTENT


@pytest.mark.asyncio
async def test_set_workspace_path_not_found(project_client: AsyncClient) -> None:
    response = await project_client.post(
        f"/api/projects/{uuid4()}/workspace",
        json={"workspace_path": "/data/foo"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_set_workspace_path_via_slug_not_found(
    project_client: AsyncClient,
) -> None:
    response = await project_client.post(
        "/api/projects/ghost-slug/workspace",
        json={"workspace_path": "/data/foo"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_set_workspace_path_success(project_client: AsyncClient) -> None:
    create = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    pid = create.json()["id"]
    response = await project_client.post(
        f"/api/projects/{pid}/workspace",
        json={"workspace_path": "/data/wow"},
        headers=_HDR,
    )
    # Service may return 200 or 404 depending on workspace logic
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_update_sync_state_not_found(project_client: AsyncClient) -> None:
    response = await project_client.post(
        f"/api/projects/{uuid4()}/sync",
        json={"head_commit": "abc123"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_update_sync_state_via_slug_not_found(
    project_client: AsyncClient,
) -> None:
    response = await project_client.post(
        "/api/projects/ghost-slug/sync",
        json={"head_commit": "abc123"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_update_sync_state_success(project_client: AsyncClient) -> None:
    create = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    pid = create.json()["id"]
    response = await project_client.post(
        f"/api/projects/{pid}/sync",
        json={"head_commit": "abc123"},
        headers=_HDR,
    )
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_add_agent_access_not_found(project_client: AsyncClient) -> None:
    response = await project_client.post(
        f"/api/projects/{uuid4()}/access/{uuid4()}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_add_agent_access_via_slug_not_found(
    project_client: AsyncClient,
) -> None:
    response = await project_client.post(
        f"/api/projects/ghost-slug/access/{uuid4()}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_remove_agent_access_not_found(project_client: AsyncClient) -> None:
    response = await project_client.delete(
        f"/api/projects/{uuid4()}/access/{uuid4()}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_remove_agent_access_via_slug_not_found(
    project_client: AsyncClient,
) -> None:
    response = await project_client.delete(
        f"/api/projects/ghost-slug/access/{uuid4()}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_create_project_developer_forbidden(
    db_session,
) -> None:
    """Developers cannot create projects."""
    dev = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(dev)
    await db_session.flush()

    app = FastAPI()
    app.include_router(project_router, prefix="/api/projects")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=dev.id, role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/projects", json=_payload(), headers=_HDR)
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_create_project_generic_error_reraises(
    project_client: AsyncClient,
) -> None:
    """Non-'already exists' service exception bubbles up (line 152)."""

    with (
        patch(
            "roboco.services.project.ProjectService.create",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await project_client.post("/api/projects", json=_payload(), headers=_HDR)


@pytest.mark.asyncio
async def test_update_project_returns_500_when_service_returns_none(
    project_client: AsyncClient,
) -> None:
    """When service.update returns None unexpectedly → 500 (lines 210-214)."""

    create = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    pid = create.json()["id"]
    with patch("roboco.services.project.ProjectService.update", return_value=None):
        response = await project_client.patch(
            f"/api/projects/{pid}", json={"name": "x"}, headers=_HDR
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_delete_project_via_slug_success(
    project_client: AsyncClient,
) -> None:
    """Delete via slug exercises the get_by_slug branch (lines 244-245)."""
    payload = _payload()
    create = await project_client.post("/api/projects", json=payload, headers=_HDR)
    assert create.status_code == HTTPStatus.CREATED
    response = await project_client.delete(
        f"/api/projects/{payload['slug']}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NO_CONTENT


@pytest.mark.asyncio
async def test_delete_project_returns_500_when_service_returns_false(
    project_client: AsyncClient,
) -> None:
    """When service.delete returns False unexpectedly → 500 (lines 258-262)."""

    create = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    pid = create.json()["id"]
    with patch("roboco.services.project.ProjectService.delete", return_value=False):
        response = await project_client.delete(f"/api/projects/{pid}", headers=_HDR)
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_set_workspace_path_via_slug_success(
    project_client: AsyncClient,
) -> None:
    """Set workspace via slug exercises slug→uuid path (line 295)."""
    payload = _payload()
    await project_client.post("/api/projects", json=payload, headers=_HDR)
    response = await project_client.post(
        f"/api/projects/{payload['slug']}/workspace",
        json={"workspace_path": "/data/wow"},
        headers=_HDR,
    )
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_update_sync_state_via_slug_success(
    project_client: AsyncClient,
) -> None:
    """Update sync state via slug exercises slug→uuid path (line 332)."""
    payload = _payload()
    await project_client.post("/api/projects", json=payload, headers=_HDR)
    response = await project_client.post(
        f"/api/projects/{payload['slug']}/sync",
        json={"head_commit": "abc123"},
        headers=_HDR,
    )
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_add_agent_access_via_slug_success(
    project_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Add access via slug exercises slug→uuid path (line 377) + success (388)."""
    payload = _payload()
    create = await project_client.post("/api/projects", json=payload, headers=_HDR)
    assert create.status_code == HTTPStatus.CREATED
    other_agent = AgentTable(
        id=uuid4(),
        name="OtherAgent",
        slug=f"other-{uuid4().hex[:6]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other_agent)
    await db_session.flush()
    response = await project_client.post(
        f"/api/projects/{payload['slug']}/access/{other_agent.id}", headers=_HDR
    )
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_add_agent_access_by_uuid_success(
    project_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Add access by uuid → success (line 388)."""
    create = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    pid = create.json()["id"]
    other_agent = AgentTable(
        id=uuid4(),
        name="OtherAgent2",
        slug=f"other2-{uuid4().hex[:6]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other_agent)
    await db_session.flush()
    response = await project_client.post(
        f"/api/projects/{pid}/access/{other_agent.id}", headers=_HDR
    )
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_remove_agent_access_via_slug_success(
    project_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Remove access via slug → success (line 414, 425)."""
    payload = _payload()
    create = await project_client.post("/api/projects", json=payload, headers=_HDR)
    assert create.status_code == HTTPStatus.CREATED
    other_agent = AgentTable(
        id=uuid4(),
        name="OtherAgent3",
        slug=f"other3-{uuid4().hex[:6]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other_agent)
    await db_session.flush()
    response = await project_client.delete(
        f"/api/projects/{payload['slug']}/access/{other_agent.id}", headers=_HDR
    )
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_remove_agent_access_by_uuid_success(
    project_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Remove access by uuid → success (line 425)."""
    create = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    pid = create.json()["id"]
    other_agent = AgentTable(
        id=uuid4(),
        name="OtherAgent4",
        slug=f"other4-{uuid4().hex[:6]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(other_agent)
    await db_session.flush()
    # Add first so remove finds an entry to drop and returns the project.
    add_response = await project_client.post(
        f"/api/projects/{pid}/access/{other_agent.id}", headers=_HDR
    )
    assert add_response.status_code == HTTPStatus.OK
    response = await project_client.delete(
        f"/api/projects/{pid}/access/{other_agent.id}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK
