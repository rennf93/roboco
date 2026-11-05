"""POST /projects/{id}/base-red/clear: role gate, 404, 501, audited 204."""

from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.project import router as project_router
from roboco.api.schemas.project import ClearBaseRedRequest
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services.gateway.base_red_gate import AUDIT_EVENT_CLEARED

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ROUTES = "roboco.api.routes.project"
REASON = {"reason": "base is green again after manual fix"}


class _Harness:
    def __init__(self) -> None:
        self.role = AgentRole.MAIN_PM
        self.agent_id = uuid4()
        self.db = MagicMock(commit=AsyncMock())
        self.app = FastAPI()
        self.app.include_router(project_router, prefix="/api/projects")
        self.app.dependency_overrides[get_db] = self._db
        self.app.dependency_overrides[get_agent_context] = self._agent

    async def _db(self) -> Any:
        return self.db

    async def _agent(self) -> AgentContext:
        return AgentContext(agent_id=self.agent_id, role=self.role, team=None)


@pytest_asyncio.fixture
async def harness() -> AsyncIterator[_Harness]:
    yield _Harness()


async def _post(harness: _Harness, project_id: str = "p-1") -> Any:
    transport = ASGITransport(app=harness.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            f"/api/projects/{project_id}/base-red/clear", json=REASON
        )


@pytest.mark.asyncio
async def test_non_pm_gets_403(harness: _Harness) -> None:
    harness.role = AgentRole.DEVELOPER
    resp = await _post(harness)
    assert resp.status_code == HTTPStatus.FORBIDDEN
    harness.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_project_404(harness: _Harness) -> None:
    missing = AsyncMock(side_effect=HTTPException(status_code=HTTPStatus.NOT_FOUND))
    with patch(f"{ROUTES}._get_project_or_404", missing):
        resp = await _post(harness)
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_absent_incident_service_501(harness: _Harness) -> None:
    project = SimpleNamespace(id=uuid4())
    with (
        patch(f"{ROUTES}._get_project_or_404", AsyncMock(return_value=project)),
        patch(f"{ROUTES}.resolve_incident_service", return_value=None),
    ):
        resp = await _post(harness)
    assert resp.status_code == HTTPStatus.NOT_IMPLEMENTED
    harness.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_clears_audits_and_commits(harness: _Harness) -> None:
    project = SimpleNamespace(id=uuid4())
    source = AsyncMock()
    audit = AsyncMock()
    with (
        patch(f"{ROUTES}._get_project_or_404", AsyncMock(return_value=project)),
        patch(f"{ROUTES}.resolve_incident_service", return_value=source),
        patch(f"{ROUTES}.get_audit_service", return_value=audit),
    ):
        resp = await _post(harness)
    assert resp.status_code == HTTPStatus.NO_CONTENT
    source.clear.assert_awaited_once_with(
        project.id, str(harness.agent_id), REASON["reason"]
    )
    kwargs = audit.log_event.await_args.kwargs
    assert kwargs["event_type"] == AUDIT_EVENT_CLEARED
    assert kwargs["agent_id"] == str(harness.agent_id)
    harness.db.commit.assert_awaited_once()


def test_reason_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        ClearBaseRedRequest(reason="short")
