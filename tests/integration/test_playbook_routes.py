"""Playbook curation routes — Auditor/CEO list + approve/reject; others 403."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.playbooks import router as playbooks_router
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.models.playbook import PlaybookCreate
from roboco.services.playbook import PlaybookService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(playbooks_router, prefix="/api/playbooks")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent_id, role=role, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


async def _seed_draft(db_session: AsyncSession, title: str = "Retry flaky pg") -> UUID:
    pb = await PlaybookService(db_session).draft(
        PlaybookCreate(
            title=title,
            problem="connection resets intermittently",
            procedure="1. retry with backoff",
            tags=["backend"],
        ),
        created_by=uuid4(),
    )
    return pb.id


@pytest_asyncio.fixture
async def auditor_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = _build_app(db_session, AgentRole.AUDITOR, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_drafts_as_auditor(
    db_session: AsyncSession, auditor_client: AsyncClient
) -> None:
    await _seed_draft(db_session, title="Draft to list")
    resp = await auditor_client.get("/api/playbooks", params={"status": "draft"})
    assert resp.status_code == HTTPStatus.OK
    titles = [p["title"] for p in resp.json()]
    assert "Draft to list" in titles


@pytest.mark.asyncio
async def test_approve_as_auditor_flips_to_approved(
    db_session: AsyncSession, auditor_client: AsyncClient
) -> None:
    pid = await _seed_draft(db_session, title="Approve me")
    resp = await auditor_client.post(f"/api/playbooks/{pid}/approve")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_reject_as_auditor_archives(
    db_session: AsyncSession, auditor_client: AsyncClient
) -> None:
    pid = await _seed_draft(db_session, title="Reject me")
    resp = await auditor_client.post(
        f"/api/playbooks/{pid}/reject", json={"reason": "duplicate of an existing one"}
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_approve_missing_is_404(auditor_client: AsyncClient) -> None:
    resp = await auditor_client.post(f"/api/playbooks/{uuid4()}/approve")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_non_curator_is_forbidden(db_session: AsyncSession) -> None:
    pid = await _seed_draft(db_session, title="Guarded")
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        get_resp = await client.get("/api/playbooks")
        approve_resp = await client.post(f"/api/playbooks/{pid}/approve")
    assert get_resp.status_code == HTTPStatus.FORBIDDEN
    assert approve_resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()
