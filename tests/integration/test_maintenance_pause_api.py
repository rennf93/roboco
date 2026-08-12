"""Maintenance Pause API: CEO-only read/set/clear per scope.

Mirrors ``tests/integration/test_board_programs_api.py``'s CEO-gating shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
import roboco.api.routes.maintenance_pause as maintenance_pause_routes
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.maintenance_pause import router as maintenance_pause_router
from roboco.db.tables import SystemSettingTable
from roboco.foundation.policy.maintenance_pause import PauseScope
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from sqlalchemy import delete

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture(autouse=True)
async def _purge_maintenance_pause_rows(
    db_session: AsyncSession,
) -> AsyncIterator[None]:
    """Write routes here commit explicitly, so a pause/resume a test writes
    would otherwise outlive it in the shared, cross-test-persistent DB and
    poison a sibling test's "unset scope" assertions (same fixed 3 keys)."""
    yield
    await db_session.execute(
        delete(SystemSettingTable).where(
            SystemSettingTable.key.like("maintenance_pause.%")
        )
    )
    await db_session.commit()


def _build_app(db_session: AsyncSession, role: AgentRole) -> FastAPI:
    app = FastAPI()
    app.include_router(maintenance_pause_router, prefix="/api/maintenance-pause")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=uuid4(), role=role, team=None, slug="ceo")

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


async def _client_for(db_session: AsyncSession, role: AgentRole) -> AsyncClient:
    app = _build_app(db_session, role)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_list_returns_all_three_scopes_unset(db_session: AsyncSession) -> None:
    async with await _client_for(db_session, AgentRole.CEO) as client:
        resp = await client.get("/api/maintenance-pause")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert {row["scope"] for row in body} == {"dispatch", "board_programs", "engines"}
    assert all(row["paused"] is False for row in body)
    assert all(row["read_degraded_since"] is None for row in body)


@pytest.mark.asyncio
async def test_list_surfaces_a_degraded_scope_distinct_from_a_human_pause(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEFECT 3: a scope whose runtime gate (``is_paused``) is CURRENTLY
    reading fail-closed must be visible on this same route even though its
    own read here succeeds and genuinely reports ``paused: false`` -- the
    phantom-pause case a fail-closed decision with no persisted trace
    would otherwise hide from the CEO entirely."""
    since = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(
        maintenance_pause_routes,
        "lookup_degraded_at",
        lambda scope: since if scope is PauseScope.DISPATCH else None,
    )

    async with await _client_for(db_session, AgentRole.CEO) as client:
        resp = await client.get("/api/maintenance-pause")

    assert resp.status_code == HTTPStatus.OK
    rows = {row["scope"]: row for row in resp.json()}
    dispatch_row = rows["dispatch"]
    assert dispatch_row["paused"] is False  # the stored setting genuinely isn't paused
    assert dispatch_row["read_degraded_since"] == since.isoformat()
    # A scope with no degraded read stays clean -- both branches covered.
    assert rows["engines"]["read_degraded_since"] is None


@pytest.mark.asyncio
async def test_pause_then_list_reflects_it(db_session: AsyncSession) -> None:
    async with await _client_for(db_session, AgentRole.CEO) as client:
        post = await client.post(
            "/api/maintenance-pause/dispatch",
            json={"reason": "NAS reboot", "hours": 2},
        )
        assert post.status_code == HTTPStatus.OK
        body = post.json()
        assert body["paused"] is True
        assert body["paused_by"] == "ceo"
        assert body["reason"] == "NAS reboot"
        assert body["expires_at"] is not None
        assert body["paused_at"] is not None

        listed = await client.get("/api/maintenance-pause")
    row = next(r for r in listed.json() if r["scope"] == "dispatch")
    assert row["paused"] is True
    assert row["reason"] == "NAS reboot"


@pytest.mark.asyncio
async def test_pause_defaults_reason_to_none_and_hours_to_default(
    db_session: AsyncSession,
) -> None:
    async with await _client_for(db_session, AgentRole.CEO) as client:
        resp = await client.post("/api/maintenance-pause/board_programs", json={})
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["paused"] is True
    assert body["reason"] is None


@pytest.mark.asyncio
async def test_pause_rejects_zero_hours(db_session: AsyncSession) -> None:
    async with await _client_for(db_session, AgentRole.CEO) as client:
        resp = await client.post("/api/maintenance-pause/engines", json={"hours": 0})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_pause_unknown_scope_is_unprocessable(db_session: AsyncSession) -> None:
    async with await _client_for(db_session, AgentRole.CEO) as client:
        resp = await client.post("/api/maintenance-pause/not_a_scope", json={})
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_delete_resumes_and_is_idempotent(db_session: AsyncSession) -> None:
    async with await _client_for(db_session, AgentRole.CEO) as client:
        await client.post("/api/maintenance-pause/engines", json={"hours": 1})
        first = await client.delete("/api/maintenance-pause/engines")
        assert first.status_code == HTTPStatus.OK
        assert first.json()["paused"] is False

        second = await client.delete("/api/maintenance-pause/engines")
        assert second.status_code == HTTPStatus.OK
        assert second.json()["paused"] is False


@pytest.mark.asyncio
async def test_non_ceo_cannot_read(db_session: AsyncSession) -> None:
    async with await _client_for(db_session, AgentRole.DEVELOPER) as client:
        resp = await client.get("/api/maintenance-pause")
    assert resp.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_non_ceo_cannot_pause(db_session: AsyncSession) -> None:
    async with await _client_for(db_session, AgentRole.DEVELOPER) as client:
        resp = await client.post("/api/maintenance-pause/dispatch", json={})
    assert resp.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_non_ceo_cannot_resume(db_session: AsyncSession) -> None:
    async with await _client_for(db_session, AgentRole.DEVELOPER) as client:
        resp = await client.delete("/api/maintenance-pause/dispatch")
    assert resp.status_code == HTTPStatus.FORBIDDEN
