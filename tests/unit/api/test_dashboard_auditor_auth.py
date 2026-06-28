"""Dashboard auditor flag/report mutating routes (``create_auditor_flag``,
``resolve_auditor_flag``, ``create_auditor_report``, ``send_auditor_report``)
are gated to AUDITOR or CEO via a ``CurrentAgentContext`` dependency plus a
coarse role gate, mirroring ``roboco/api/routes/playbooks.py::_require_curator``.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.dashboard import router as dashboard_router
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services.dashboard import reset_storage

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _override_agent(role: AgentRole) -> AgentContext:
    return AgentContext(agent_id=uuid4(), role=role, team=None)


@pytest_asyncio.fixture
async def auditor_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """A client authenticated as the Auditor (the legitimate caller)."""
    reset_storage()
    app = FastAPI()
    app.include_router(dashboard_router, prefix="/api/dashboard")

    async def _override_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = lambda: _override_agent(
        AgentRole.AUDITOR
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def dev_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """A client authenticated as a Developer — must NOT be able to mutate
    auditor flags/reports."""
    reset_storage()
    app = FastAPI()
    app.include_router(dashboard_router, prefix="/api/dashboard")

    async def _override_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = lambda: _override_agent(
        AgentRole.DEVELOPER
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Legitimate caller (Auditor) succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auditor_can_create_flag(auditor_client: AsyncClient) -> None:
    response = await auditor_client.post(
        "/api/dashboard/auditor/flags",
        json={
            "severity": "warning",
            "category": "quality",
            "title": "Flag",
            "description": "x",
        },
    )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_auditor_can_create_report(auditor_client: AsyncClient) -> None:
    response = await auditor_client.post(
        "/api/dashboard/auditor/reports",
        json={
            "report_type": "weekly",
            "title": "T",
            "summary": "s",
            "sections": [],
        },
    )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_auditor_can_send_report(auditor_client: AsyncClient) -> None:
    create = await auditor_client.post(
        "/api/dashboard/auditor/reports",
        json={
            "report_type": "weekly",
            "title": "T",
            "summary": "s",
            "sections": [],
        },
    )
    rid = create.json()["id"]
    response = await auditor_client.post(f"/api/dashboard/auditor/reports/{rid}/send")
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_auditor_can_resolve_flag(auditor_client: AsyncClient) -> None:
    create = await auditor_client.post(
        "/api/dashboard/auditor/flags",
        json={
            "severity": "warning",
            "category": "quality",
            "title": "F",
            "description": "x",
        },
    )
    flag_id = create.json()["id"]
    response = await auditor_client.put(
        f"/api/dashboard/auditor/flags/{flag_id}/resolve",
        params={"notes": "fixed"},
    )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Forged caller (Developer) is rejected with 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_developer_cannot_create_flag(dev_client: AsyncClient) -> None:
    response = await dev_client.post(
        "/api/dashboard/auditor/flags",
        json={
            "severity": "warning",
            "category": "quality",
            "title": "F",
            "description": "x",
        },
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_developer_cannot_resolve_flag(dev_client: AsyncClient) -> None:
    # The role gate fires before the route checks flag existence, so a random
    # UUID is enough to prove the dev is rejected at the gate.
    response = await dev_client.put(
        f"/api/dashboard/auditor/flags/{uuid4()}/resolve",
        params={"notes": "fixed"},
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_developer_cannot_create_report(dev_client: AsyncClient) -> None:
    response = await dev_client.post(
        "/api/dashboard/auditor/reports",
        json={
            "report_type": "weekly",
            "title": "T",
            "summary": "s",
            "sections": [],
        },
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_developer_cannot_send_report(dev_client: AsyncClient) -> None:
    response = await dev_client.post(
        f"/api/dashboard/auditor/reports/{uuid4()}/send",
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
