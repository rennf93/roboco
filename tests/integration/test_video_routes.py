"""Video engine route coverage — the on-demand request trigger, the held
video_post draft list/approve/reject queue, and the TikTok credentials
sub-router. CEO-only throughout."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.video import router as video_router
from roboco.api.routes.video import tiktok_router
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services.heartbeat_mutex import HeartbeatMutex
from roboco.services.task import VIDEO_POST_SOURCE, VIDEO_SOURCE, get_task_service
from roboco.services.tiktok_credentials import get_tiktok_credentials_service
from roboco.services.video_post_service import XVideoPostResult
from roboco.services.x_credentials import get_x_credentials_service
from roboco.services.x_video_client import LiveXVideoPoster
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


async def _seed_agent(session: AsyncSession, role: AgentRole, slug: str) -> AgentTable:
    agent = AgentTable(
        id=uuid4(),
        name=slug,
        slug=f"{slug}-{uuid4().hex[:6]}",
        role=role,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.flush()
    return agent


async def _seed_draft(
    session: AsyncSession, *, platforms: list[str] | None = None
) -> TaskTable:
    """A held ``video_post`` draft — the approve/reject/list queue basis."""
    system = await _seed_agent(session, AgentRole.SYSTEM, "system")
    secretary = await _seed_agent(session, AgentRole.SECRETARY, "secretary")
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo",
        slug=f"roboco-{uuid4().hex[:6]}",
        git_url="https://example.com/roboco.git",
        assigned_cell=Team.BACKEND,
        created_by=system.id,
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title="Video post: release 1.0",
        description="script",
        acceptance_criteria=["CEO approves or rejects the draft"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        project_id=project.id,
        created_by=system.id,
        assigned_to=secretary.id,
        team=Team.MAIN_PM,
        source=VIDEO_POST_SOURCE,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    markers.set_video_draft(
        task,
        {
            "occasion": "release 1.0",
            "script": "script",
            "platforms": platforms if platforms is not None else ["x"],
            "mp4_paths": {
                "square": "/render/out/1-square.mp4",
                "vertical": "/render/out/1-vertical.mp4",
            },
            "x_caption": "Check out this clip",
            "tiktok_caption": "Check out this clip on TikTok",
            "render_status": "rendered",
        },
    )
    await session.flush()
    return task


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(video_router, prefix="/api/video")
    app.include_router(tiktok_router, prefix="/api/tiktok")

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


_LOCKED = (
    patch.object(HeartbeatMutex, "acquire", AsyncMock(return_value="tok")),
    patch.object(HeartbeatMutex, "release", AsyncMock(return_value=None)),
)


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
async def test_list_posts_returns_open_draft(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)
    resp = await ceo_client.get("/api/video/posts")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == 1
    assert body[0]["task_id"] == str(task.id)
    assert body[0]["occasion"] == "release 1.0"
    assert body[0]["platforms"] == ["x"]


@pytest.mark.asyncio
async def test_approve_without_credentials_fails_gracefully(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """No X/TikTok credentials configured in this test DB: the route still
    builds real (Null) posters and the approve completes without raising —
    just with nothing posted."""
    task = await _seed_draft(db_session, platforms=["x", "tiktok"])
    with _LOCKED[0], _LOCKED[1]:
        resp = await ceo_client.post(f"/api/video/posts/{task.id}/approve", json={})
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "post_failed"
    assert body["posted"] == {}
    await db_session.refresh(task)
    assert task.status == TaskStatus.PENDING  # never advanced without a real post


@pytest.mark.asyncio
async def test_approve_with_credentials_posts_via_the_real_poster_wiring(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """Once X credentials are configured, the route builds a LiveXVideoPoster
    (not the Null default) — the network call itself is mocked here; the
    real HTTP sequence is covered by test_x_video_client.py.

    The approve route commits durably (mirrors the X-post pattern), so the
    x_credentials singleton row must be cleared afterward — left behind, it
    leaks into any later test in this shared session-scoped test DB that
    asserts a fresh "unset" state (e.g. test_x_credentials_service.py)."""
    task = await _seed_draft(db_session, platforms=["x"])
    creds_svc = get_x_credentials_service(db_session)
    await creds_svc.set_credentials(
        api_key="ak", api_secret="as", access_token="at", access_token_secret="ats"
    )
    try:
        with (
            _LOCKED[0],
            _LOCKED[1],
            patch.object(
                LiveXVideoPoster,
                "post_video",
                AsyncMock(
                    return_value=XVideoPostResult(
                        posted=True, video_id="xid1", detail="posted"
                    )
                ),
            ),
        ):
            resp = await ceo_client.post(f"/api/video/posts/{task.id}/approve", json={})
        assert resp.status_code == HTTPStatus.OK
        body = resp.json()
        assert body["status"] == "posted"
        assert body["posted"] == {"x": "xid1"}
        await db_session.refresh(task)
        assert task.status == TaskStatus.COMPLETED
    finally:
        await creds_svc.set_credentials(
            api_key="", api_secret="", access_token="", access_token_secret=""
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_approve_edited_x_caption_over_limit_is_422(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)
    resp = await ceo_client.post(
        f"/api/video/posts/{task.id}/approve", json={"x_caption": "x" * 281}
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_approve_missing_task_is_404(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.post(f"/api/video/posts/{uuid4()}/approve", json={})
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_reject_cancels_and_records_reason(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)
    resp = await ceo_client.post(
        f"/api/video/posts/{task.id}/reject", json={"reason": "Not our voice"}
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["reject_reason"] == "Not our voice"
    refreshed = await db_session.get(TaskTable, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_reject_missing_task_is_404(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.post(
        f"/api/video/posts/{uuid4()}/reject", json={"reason": "not relevant here"}
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_tiktok_credentials_default_is_unset(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get("/api/tiktok/credentials")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["has_credentials"] is False


@pytest.mark.asyncio
async def test_set_tiktok_credentials_reports_status_never_plaintext(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """The route commits durably, so the tiktok_credentials singleton row is
    cleared afterward — left behind, it leaks into any later test in this
    shared session-scoped test DB (e.g. test_tiktok_credentials_service.py's
    "unset" assertions)."""
    try:
        resp = await ceo_client.post(
            "/api/tiktok/credentials",
            json={
                "client_key": "secret-key-value",
                "client_secret": "secret-clientsecret-value",
                "access_token": "secret-token-value",
                "refresh_token": "secret-refresh-value",
            },
        )
        assert resp.status_code == HTTPStatus.OK
        assert resp.json() == {"has_credentials": True}
        assert "secret-key-value" not in resp.text
        assert "secret-clientsecret-value" not in resp.text
        assert "secret-token-value" not in resp.text
        assert "secret-refresh-value" not in resp.text

        status_resp = await ceo_client.get("/api/tiktok/credentials")
        assert status_resp.json()["has_credentials"] is True
    finally:
        await get_tiktok_credentials_service(db_session).set_credentials(
            client_key="", client_secret="", access_token="", refresh_token=""
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_set_tiktok_credentials_partial_is_400(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.post(
        "/api/tiktok/credentials",
        json={
            "client_key": "only-one",
            "client_secret": "",
            "access_token": "",
            "refresh_token": "",
        },
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed(db_session)
    await _seed_draft(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        request_resp = await client.post(
            "/api/video/request",
            json={"occasion": "occ", "brief": "brief", "platforms": ["x"]},
        )
        list_resp = await client.get("/api/video/posts")
        creds_resp = await client.get("/api/tiktok/credentials")
    assert request_resp.status_code == HTTPStatus.FORBIDDEN
    assert list_resp.status_code == HTTPStatus.FORBIDDEN
    assert creds_resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()
