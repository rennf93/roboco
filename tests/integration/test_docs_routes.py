"""Docs API route coverage."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.docs import router as docs_router
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.permissions import AgentContext
from roboco.models.task import DocRef
from roboco.services.base import NotFoundError, UnauthorizedError, ValidationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def docs_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    agent = AgentTable(
        id=uuid4(),
        name="Doc",
        slug=f"be-doc-{uuid4().hex[:8]}",
        role=AgentRole.DOCUMENTER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="doc",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()

    app = FastAPI()
    app.include_router(docs_router, prefix="/api/docs")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=agent.id, role=AgentRole.DOCUMENTER, team=Team.BACKEND
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "documenter"}


@pytest.mark.asyncio
async def test_write_doc_validation_error(docs_client: AsyncClient) -> None:
    """Service raises ValidationError → 400."""
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.write_doc = AsyncMock(side_effect=ValidationError("bad input"))
        mock_get.return_value = mock_service
        response = await docs_client.post(
            "/api/docs/write",
            json={
                "task_id": str(uuid4()),
                "filename": "test.md",
                "doc_type": "api",
                "title": "Test",
                "content": "Some content",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_write_doc_unauthorized(docs_client: AsyncClient) -> None:
    """Service raises UnauthorizedError → 403."""
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.write_doc = AsyncMock(side_effect=UnauthorizedError("not allowed"))
        mock_get.return_value = mock_service
        response = await docs_client.post(
            "/api/docs/write",
            json={
                "task_id": str(uuid4()),
                "filename": "test.md",
                "doc_type": "api",
                "title": "Test",
                "content": "Some content",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_write_doc_unauthorized_envelope_remediate(
    docs_client: AsyncClient,
) -> None:
    """A denied write returns an Envelope-shaped body with a real remediate.

    The docs RBAC decision lives in the service (UnauthorizedError); the
    route must surface it as the gateway Envelope contract — error +
    non-null remediate — not a bare {"detail": ...} HTTPException body.
    """
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.write_doc = AsyncMock(side_effect=UnauthorizedError("write_doc"))
        mock_get.return_value = mock_service
        response = await docs_client.post(
            "/api/docs/write",
            json={
                "task_id": str(uuid4()),
                "filename": "test.md",
                "doc_type": "api",
                "title": "Test",
                "content": "Some content",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.FORBIDDEN
    body = response.json()
    assert body["error"] == "not_authorized"
    assert body["message"]
    assert body["remediate"] is not None
    assert body["remediate"].strip()


@pytest.mark.asyncio
async def test_write_doc_not_found(docs_client: AsyncClient) -> None:
    """Service raises NotFoundError → 404."""
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.write_doc = AsyncMock(side_effect=NotFoundError("not found"))
        mock_get.return_value = mock_service
        response = await docs_client.post(
            "/api/docs/write",
            json={
                "task_id": str(uuid4()),
                "filename": "test.md",
                "doc_type": "api",
                "title": "Test",
                "content": "Some content",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_write_doc_success(docs_client: AsyncClient) -> None:
    """Happy path → 200."""
    doc_ref = DocRef(
        path="backend/api/test.md",
        title="Test",
        doc_type="api",
        version="1",
        created_by="be-doc-1",
        created_at="2026-01-01T00:00:00Z",
    )
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.write_doc = AsyncMock(
            return_value=("backend/api/test.md", doc_ref, False)
        )
        mock_get.return_value = mock_service
        response = await docs_client.post(
            "/api/docs/write",
            json={
                "task_id": str(uuid4()),
                "filename": "test.md",
                "doc_type": "api",
                "title": "Test",
                "content": "Some content",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == "created"


@pytest.mark.asyncio
async def test_write_doc_update_path(docs_client: AsyncClient) -> None:
    """Update path returns status=updated."""
    doc_ref = DocRef(
        path="backend/api/test.md",
        title="Test",
        doc_type="api",
        version="2",
    )
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.write_doc = AsyncMock(
            return_value=("backend/api/test.md", doc_ref, True)
        )
        mock_get.return_value = mock_service
        response = await docs_client.post(
            "/api/docs/write",
            json={
                "task_id": str(uuid4()),
                "filename": "test.md",
                "doc_type": "api",
                "title": "Test",
                "content": "Some content",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "updated"


@pytest.mark.asyncio
async def test_read_doc_validation(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.read_doc = AsyncMock(side_effect=ValidationError("bad path"))
        mock_get.return_value = mock_service
        response = await docs_client.get(
            "/api/docs/read?path=invalid",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_read_doc_unauthorized(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.read_doc = AsyncMock(side_effect=UnauthorizedError("denied"))
        mock_get.return_value = mock_service
        response = await docs_client.get(
            "/api/docs/read?path=other/team/x.md", headers=_HDR
        )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_read_doc_not_found(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.read_doc = AsyncMock(side_effect=NotFoundError("missing"))
        mock_get.return_value = mock_service
        response = await docs_client.get(
            "/api/docs/read?path=backend/spec/x.md", headers=_HDR
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_read_doc_success(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.read_doc = AsyncMock(return_value=("# Hello", 7))
        mock_get.return_value = mock_service
        response = await docs_client.get(
            "/api/docs/read?path=backend/spec/x.md", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["content"] == "# Hello"


@pytest.mark.asyncio
async def test_list_docs_unauthorized(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.list_docs = AsyncMock(side_effect=UnauthorizedError("nope"))
        mock_get.return_value = mock_service
        response = await docs_client.get("/api/docs/list", headers=_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_list_docs_not_found(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.list_docs = AsyncMock(side_effect=NotFoundError("none"))
        mock_get.return_value = mock_service
        response = await docs_client.get("/api/docs/list", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_list_docs_success(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.list_docs = AsyncMock(return_value=[])
        mock_get.return_value = mock_service
        with patch("roboco.api.routes.docs.get_agent_team", return_value="backend"):
            response = await docs_client.get("/api/docs/list", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_delete_doc_validation(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.delete_doc = AsyncMock(side_effect=ValidationError("bad"))
        mock_get.return_value = mock_service
        response = await docs_client.delete("/api/docs/delete?path=bad", headers=_HDR)
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_delete_doc_unauthorized(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.delete_doc = AsyncMock(side_effect=UnauthorizedError("nope"))
        mock_get.return_value = mock_service
        response = await docs_client.delete("/api/docs/delete?path=x.md", headers=_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_delete_doc_not_found(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.delete_doc = AsyncMock(side_effect=NotFoundError("missing"))
        mock_get.return_value = mock_service
        response = await docs_client.delete("/api/docs/delete?path=x.md", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_delete_doc_success(docs_client: AsyncClient) -> None:
    with patch("roboco.api.routes.docs.get_docs_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.delete_doc = AsyncMock(return_value=None)
        mock_get.return_value = mock_service
        response = await docs_client.delete("/api/docs/delete?path=x.md", headers=_HDR)
    assert response.status_code == HTTPStatus.NO_CONTENT
