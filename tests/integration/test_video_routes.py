"""On-demand video-request route coverage — CEO-only authoring trigger."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.video import router as video_router
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus
from roboco.models.permissions import AgentContext
from roboco.services.task import VIDEO_SOURCE, get_task_service
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

SLUG = "roboco-video-route-test"
SYSTEM_UUID = _foundation.AGENTS["system"].uuid
UX_DEV_1_UUID = _foundation.AGENTS["ux-dev-1"].uuid
UX_DEV_2_UUID = _foundation.AGENTS["ux-dev-2"].uuid


async def _seed(session: AsyncSession) -> None:
    for uuid_, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (UX_DEV_1_UUID, "ux-dev-1", AgentRole.DEVELOPER, Team.UX_UI),
        (UX_DEV_2_UUID, "ux-dev-2", AgentRole.DEVELOPER, Team.UX_UI),
    ):
        if await session.get(AgentTable, uuid_) is None:
            session.add(
                AgentTable(
                    id=uuid_,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()
    existing = await session.execute(
        select(ProjectTable).where(ProjectTable.slug == SLUG)
    )
    if existing.scalar_one_or_none() is None:
        session.add(
            ProjectTable(
                name="RoboCo",
                slug=SLUG,
                git_url="https://github.com/x/roboco.git",
                default_branch="master",
                protected_branches=["master"],
                assigned_cell=Team.BACKEND,
                created_by=SYSTEM_UUID,
                is_active=True,
            )
        )
    await session.flush()


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(video_router, prefix="/api/video")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent_id, role=role, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


@pytest_asyncio.fixture
async def ceo_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = _build_app(db_session, AgentRole.CEO, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_request_video_opens_authoring_task(
    db_session: AsyncSession, ceo_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "video_engine_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    monkeypatch.setattr(cfg, "video_max_open_posts", 5)
    resp = await ceo_client.post(
        "/api/video/request",
        json={
            "occasion": "CEO on-demand: launch teaser",
            "brief": "A short teaser for the new dashboard",
            "platforms": ["x", "tiktok"],
        },
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "opened"
    assert body["task_id"] is not None
    # The route commits (mirrors the X route), so identity/field checks on the
    # specific created row — not a global open-list count — keep this robust
    # against other committed rows in the shared session-scoped test DB.
    task = await db_session.get(TaskTable, UUID(body["task_id"]))
    assert task is not None
    assert task.source == VIDEO_SOURCE
    assert task.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_request_video_disabled_returns_clear_response(
    db_session: AsyncSession, ceo_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "video_engine_enabled", False)
    before = len(await get_task_service(db_session).list_open_video_posts())
    resp = await ceo_client.post(
        "/api/video/request",
        json={"occasion": "occ-disabled", "brief": "brief", "platforms": ["x"]},
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "disabled"
    assert body["task_id"] is None
    after = len(await get_task_service(db_session).list_open_video_posts())
    assert after == before  # nothing new was opened


@pytest.mark.asyncio
async def test_request_video_not_opened_when_project_unresolvable(
    db_session: AsyncSession, ceo_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unresolvable project makes open_video_task no-op — a clear
    ``not_opened`` response, not a 500 or a fabricated task."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "video_engine_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_project_slug", "no-such-project")
    before = len(await get_task_service(db_session).list_open_video_posts())
    resp = await ceo_client.post(
        "/api/video/request",
        json={"occasion": "occ-unresolvable", "brief": "brief", "platforms": ["x"]},
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "not_opened"
    assert body["task_id"] is None
    after = len(await get_task_service(db_session).list_open_video_posts())
    assert after == before  # nothing new was opened


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/video/request",
            json={"occasion": "occ", "brief": "brief", "platforms": ["x"]},
        )
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()
