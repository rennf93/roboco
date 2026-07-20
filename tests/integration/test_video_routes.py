"""Video engine route coverage — the on-demand request trigger, the held
video_post draft list/approve/reject queue, and the TikTok credentials
sub-router. CEO-only throughout."""

from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes import video as video_module
from roboco.api.routes.video import router as video_router
from roboco.api.routes.video import tiktok_router
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services import minio_client
from roboco.services.heartbeat_mutex import HeartbeatMutex
from roboco.services.task import VIDEO_POST_SOURCE, VIDEO_SOURCE, get_task_service
from roboco.services.tiktok_credentials import get_tiktok_credentials_service
from roboco.services.video_post_service import XVideoPostResult
from roboco.services.x_credentials import get_x_credentials_service
from roboco.services.x_video_client import LiveXVideoPoster
from sqlalchemy import delete, select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

SLUG = "roboco-video-route-test"
SYSTEM_UUID = _foundation.AGENTS["system"].uuid
UX_DEV_1_UUID = _foundation.AGENTS["ux-dev-1"].uuid
UX_DEV_2_UUID = _foundation.AGENTS["ux-dev-2"].uuid
HISTORY_LIMIT = 2
RETRY_ATTEMPTS = 2
PREVIEW_DURATION_SECONDS = 6.0


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
                video_engine_enabled=True,
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
    session: AsyncSession,
    *,
    platforms: list[str] | None = None,
    mp4_paths: dict[str, str] | None = None,
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
            "mp4_paths": mp4_paths
            if mp4_paths is not None
            else {
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


async def _seed_authoring_task(
    session: AsyncSession,
    *,
    status: TaskStatus = TaskStatus.IN_PROGRESS,
    draft_extra: dict[str, object] | None = None,
    pr_number: int | None = None,
) -> TaskTable:
    """A ``source=video`` UX/UI authoring task — the pipeline route's basis.
    Mirrors ``_seed_draft`` but for the pre-render authoring stage."""
    system = await _seed_agent(session, AgentRole.SYSTEM, "system")
    ux_dev = await _seed_agent(session, AgentRole.DEVELOPER, "ux-dev")
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo",
        slug=f"roboco-{uuid4().hex[:6]}",
        git_url="https://example.com/roboco.git",
        assigned_cell=Team.UX_UI,
        created_by=system.id,
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title="Video: launch teaser",
        description="A short teaser for the launch",
        acceptance_criteria=["dev builds the composition"],
        status=status,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        estimated_complexity=Complexity.LOW,
        project_id=project.id,
        created_by=system.id,
        assigned_to=ux_dev.id,
        team=Team.UX_UI,
        source=VIDEO_SOURCE,
        confirmed_by_human=True,
        pr_number=pr_number,
    )
    session.add(task)
    await session.flush()
    markers.set_video_draft(
        task,
        {"occasion": "launch teaser", "script": "script", **(draft_extra or {})},
    )
    await session.flush()
    return task


def _build_app(
    db_session: AsyncSession | None, role: AgentRole, agent_id: UUID
) -> FastAPI:
    app = FastAPI()
    app.include_router(video_router, prefix="/api/video")
    app.include_router(tiktok_router, prefix="/api/tiktok")

    async def _override_db() -> AsyncIterator[AsyncSession | None]:
        # DB-independent tests pass db_session=None and monkeypatch the task
        # service so the route never awaits the session — yielding None is
        # safe because the route body uses the patched service, not get_db.
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
    monkeypatch.setattr(cfg, "video_max_open_posts", 5)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    resp = await ceo_client.post(
        "/api/video/request",
        json={
            "occasion": "CEO on-demand: launch teaser",
            "brief": "A short teaser for the new dashboard",
            "platforms": ["x", "tiktok"],
            "project_id": str(project.id),
        },
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "opened"
    assert body["task_id"] is not None
    try:
        # The route commits (mirrors the X route), so identity/field checks on
        # the specific created row — not a global open-list count — keep this
        # robust against other committed rows in the shared session-scoped
        # test DB.
        task = await db_session.get(TaskTable, UUID(body["task_id"]))
        assert task is not None
        assert task.source == VIDEO_SOURCE
        assert task.status == TaskStatus.PENDING
        assert task.project_id == project.id
    finally:
        # The route's commit durably persists this task past this test's own
        # rollback teardown — a non-terminal source=video row left behind
        # pollutes every later test in this session that counts open video
        # tasks (test_video_engine.py / test_video_render_loop.py), so it
        # must be deleted explicitly, not just rolled back.
        await db_session.execute(
            delete(TaskTable).where(TaskTable.id == UUID(body["task_id"]))
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_request_video_disabled_returns_clear_response(
    db_session: AsyncSession, ceo_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "video_engine_enabled", False)
    before = len(await get_task_service(db_session).list_open_video_posts())
    resp = await ceo_client.post(
        "/api/video/request",
        json={
            "occasion": "occ-disabled",
            "brief": "brief",
            "platforms": ["x"],
            "project_id": str(uuid4()),
        },
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "disabled"
    assert body["task_id"] is None
    after = len(await get_task_service(db_session).list_open_video_posts())
    assert after == before  # nothing new was opened


@pytest.mark.asyncio
async def test_request_video_404s_on_unresolvable_project_id(
    db_session: AsyncSession, ceo_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project_id that doesn't resolve to any project 404s — no fabricated
    task, no silent not_opened."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "video_engine_enabled", True)
    before = len(await get_task_service(db_session).list_open_video_posts())
    resp = await ceo_client.post(
        "/api/video/request",
        json={
            "occasion": "occ-unresolvable",
            "brief": "brief",
            "platforms": ["x"],
            "project_id": str(uuid4()),
        },
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND
    after = len(await get_task_service(db_session).list_open_video_posts())
    assert after == before  # nothing new was opened


@pytest.mark.asyncio
async def test_request_video_404s_on_non_opted_in_project_id(
    db_session: AsyncSession, ceo_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project that resolves but hasn't flipped video_engine_enabled also
    404s, distinct from the unresolvable-project case above."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "video_engine_enabled", True)
    project = ProjectTable(
        id=uuid4(),
        name="Not Opted In",
        slug=f"not-opted-{uuid4().hex[:6]}",
        git_url="https://example.com/notopted.git",
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        video_engine_enabled=False,
    )
    db_session.add(project)
    await db_session.flush()
    resp = await ceo_client.post(
        "/api/video/request",
        json={
            "occasion": "occ-not-opted",
            "brief": "brief",
            "platforms": ["x"],
            "project_id": str(project.id),
        },
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_request_video_not_opened_on_duplicate_occasion(
    db_session: AsyncSession, ceo_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid, opted-in project_id still no-ops (not a 404) for a duplicate
    occasion — the open-cap/dedup reasons stay a clear 200 not_opened."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "video_engine_enabled", True)
    monkeypatch.setattr(cfg, "video_max_open_posts", 5)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    payload = {
        "occasion": "occ-dupe",
        "brief": "brief",
        "platforms": ["x"],
        "project_id": str(project.id),
    }
    first = await ceo_client.post("/api/video/request", json=payload)
    assert first.status_code == HTTPStatus.OK
    assert first.json()["status"] == "opened"
    task_id = first.json()["task_id"]
    try:
        second = await ceo_client.post("/api/video/request", json=payload)
        assert second.status_code == HTTPStatus.OK
        assert second.json()["status"] == "not_opened"
        assert second.json()["task_id"] is None
    finally:
        await db_session.execute(delete(TaskTable).where(TaskTable.id == UUID(task_id)))
        await db_session.commit()


@pytest.mark.asyncio
async def test_list_posts_returns_open_draft(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)
    project = await db_session.get(ProjectTable, task.project_id)
    resp = await ceo_client.get("/api/video/posts")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == 1
    assert body[0]["task_id"] == str(task.id)
    assert body[0]["occasion"] == "release 1.0"
    assert body[0]["platforms"] == ["x"]
    assert body[0]["mp4_paths"] == {
        "square": "/render/out/1-square.mp4",
        "vertical": "/render/out/1-vertical.mp4",
    }
    assert project is not None
    assert body[0]["project_slug"] == project.slug
    assert body[0]["project_name"] == project.name


@pytest.mark.asyncio
async def test_list_posts_includes_source_task_id(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """source_task_id round-trips from the marker to the response — the
    panel's basis for a future draft->authoring-task deep link."""
    source_task_id = uuid4()
    task = await _seed_draft(db_session)
    draft = markers.get_video_draft(task) or {}
    markers.set_video_draft(task, {**draft, "source_task_id": str(source_task_id)})
    await db_session.flush()
    resp = await ceo_client.get("/api/video/posts")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body[0]["source_task_id"] == str(source_task_id)


@pytest.mark.asyncio
async def test_history_includes_source_task_id(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    source_task_id = uuid4()
    task = await _seed_draft(db_session)
    draft = markers.get_video_draft(task) or {}
    markers.set_video_draft(task, {**draft, "source_task_id": str(source_task_id)})
    await db_session.flush()
    with _LOCKED[0], _LOCKED[1]:
        await ceo_client.post(
            f"/api/video/posts/{task.id}/reject", json={"reason": "off-brand"}
        )
    resp = await ceo_client.get("/api/video/posts/history")
    body = resp.json()
    row = next(r for r in body if r["task_id"] == str(task.id))
    assert row["source_task_id"] == str(source_task_id)


# --- pipeline strip (task 1, 2026-07-09) --------------------------------------


@pytest.mark.asyncio
async def test_pipeline_lists_non_terminal_authoring_task(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_authoring_task(db_session, status=TaskStatus.IN_PROGRESS)
    project = await db_session.get(ProjectTable, task.project_id)
    resp = await ceo_client.get("/api/video/pipeline")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    row = next(r for r in body if r["task_id"] == str(task.id))
    assert row["status"] == "in_progress"
    assert row["occasion"] == "launch teaser"
    assert row["render_status"] is None
    assert row["render_attempts"] == 0
    assert row["max_attempts"] == markers.MAX_VIDEO_RENDER_ATTEMPTS
    assert row["render_error"] is None
    assert project is not None
    assert row["project_slug"] == project.slug
    assert row["project_name"] == project.name


@pytest.mark.asyncio
async def test_pipeline_shows_completed_unrendered_with_attempts(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """A COMPLETED authoring task the render loop hasn't finished with
    (render_status unset) stays visible with its retry count."""
    task = await _seed_authoring_task(
        db_session,
        status=TaskStatus.COMPLETED,
        draft_extra={"composition_id": "Intro", "render_attempts": RETRY_ATTEMPTS},
    )
    resp = await ceo_client.get("/api/video/pipeline")
    body = resp.json()
    row = next(r for r in body if r["task_id"] == str(task.id))
    assert row["render_attempts"] == RETRY_ATTEMPTS
    assert row["render_status"] is None
    assert row["composition_id"] == "Intro"


@pytest.mark.asyncio
async def test_pipeline_shows_failed_render_with_error(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_authoring_task(
        db_session,
        status=TaskStatus.COMPLETED,
        draft_extra={
            "composition_id": "Intro",
            "render_status": "failed",
            "render_attempts": markers.MAX_VIDEO_RENDER_ATTEMPTS,
            "render_error": "sidecar timeout",
        },
    )
    resp = await ceo_client.get("/api/video/pipeline")
    body = resp.json()
    row = next(r for r in body if r["task_id"] == str(task.id))
    assert row["render_status"] == "failed"
    assert row["render_attempts"] == markers.MAX_VIDEO_RENDER_ATTEMPTS
    assert row["render_error"] == "sidecar timeout"


@pytest.mark.asyncio
async def test_pipeline_excludes_rendered_completed_task(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """A rendered task already materialized its video_post draft — it must
    not double-appear in the pipeline strip."""
    task = await _seed_authoring_task(
        db_session,
        status=TaskStatus.COMPLETED,
        draft_extra={"composition_id": "Intro", "render_status": "rendered"},
    )
    resp = await ceo_client.get("/api/video/pipeline")
    ids = [row["task_id"] for row in resp.json()]
    assert str(task.id) not in ids


@pytest.mark.asyncio
async def test_pipeline_excludes_cancelled_task(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_authoring_task(db_session, status=TaskStatus.CANCELLED)
    resp = await ceo_client.get("/api/video/pipeline")
    ids = [row["task_id"] for row in resp.json()]
    assert str(task.id) not in ids


@pytest.mark.asyncio
async def test_pipeline_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/video/pipeline")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_media_returns_the_rendered_cut(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "video_output_dir", str(tmp_path))
    vertical = tmp_path / "clip-vertical.mp4"
    vertical.write_bytes(b"fake-mp4-bytes-vertical")
    task = await _seed_draft(
        db_session,
        mp4_paths={"vertical": str(vertical), "square": str(tmp_path / "missing.mp4")},
    )
    resp = await ceo_client.get(f"/api/video/posts/{task.id}/media?cut=vertical")
    assert resp.status_code == HTTPStatus.OK
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == b"fake-mp4-bytes-vertical"


@pytest.mark.asyncio
async def test_media_outside_output_dir_is_404(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mp4_paths entry that resolves outside video_output_dir is refused
    even though the file exists on disk — defense-in-depth against any
    future writer of mp4_paths."""
    outside = tmp_path / "outside" / "clip-vertical.mp4"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"fake-mp4-bytes")
    monkeypatch.setattr(cfg, "video_output_dir", str(tmp_path / "confined"))
    task = await _seed_draft(db_session, mp4_paths={"vertical": str(outside)})
    resp = await ceo_client.get(f"/api/video/posts/{task.id}/media?cut=vertical")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_media_bad_cut_is_400(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)
    resp = await ceo_client.get(f"/api/video/posts/{task.id}/media?cut=diagonal")
    assert resp.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_media_missing_task_is_404(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get(f"/api/video/posts/{uuid4()}/media?cut=vertical")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_media_unrendered_cut_is_404(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """The seeded draft's paths never exist on disk — a 404, not a crash."""
    task = await _seed_draft(db_session)
    resp = await ceo_client.get(f"/api/video/posts/{task.id}/media?cut=square")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_approve_without_credentials_fails_gracefully(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """No X/TikTok credentials configured in this test DB: the route still
    builds real (Null) posters and the approve completes without raising —
    just with nothing posted."""
    task = await _seed_draft(db_session, platforms=["x", "tiktok"])
    try:
        with _LOCKED[0], _LOCKED[1]:
            resp = await ceo_client.post(f"/api/video/posts/{task.id}/approve", json={})
        assert resp.status_code == HTTPStatus.OK
        body = resp.json()
        assert body["status"] == "post_failed"
        assert body["posted"] == {}
        await db_session.refresh(task)
        assert task.status == TaskStatus.PENDING  # never advanced without a real post
    finally:
        # The approve route commits durably even on a post_failed outcome, so
        # this non-terminal source=video_post row survives this test's own
        # rollback teardown — left behind, it pollutes every later test in
        # this session that counts open video tasks.
        await db_session.execute(delete(TaskTable).where(TaskTable.id == task.id))
        await db_session.commit()


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
async def test_history_returns_posted_and_rejected_newest_first(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    rejected = await _seed_draft(db_session, platforms=["x"])
    with _LOCKED[0], _LOCKED[1]:
        await ceo_client.post(
            f"/api/video/posts/{rejected.id}/reject",
            json={"reason": "wrong occasion"},
        )
    posted = await _seed_draft(db_session, platforms=["x"])
    posted_project = await db_session.get(ProjectTable, posted.project_id)
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
                        posted=True, video_id="xid42", detail="posted"
                    )
                ),
            ),
        ):
            await ceo_client.post(f"/api/video/posts/{posted.id}/approve", json={})

        resp = await ceo_client.get("/api/video/posts/history")
        assert resp.status_code == HTTPStatus.OK
        body = resp.json()
        ids = [row["task_id"] for row in body]
        assert str(posted.id) in ids
        assert str(rejected.id) in ids
        assert ids.index(str(posted.id)) < ids.index(str(rejected.id))
        posted_row = next(row for row in body if row["task_id"] == str(posted.id))
        assert posted_row["status"] == "completed"
        assert posted_row["posted"] == {"x": "xid42"}
        assert posted_project is not None
        assert posted_row["project_slug"] == posted_project.slug
        assert posted_row["project_name"] == posted_project.name
        rejected_row = next(row for row in body if row["task_id"] == str(rejected.id))
        assert rejected_row["status"] == "cancelled"
        assert rejected_row["reject_reason"] == "wrong occasion"
    finally:
        await creds_svc.set_credentials(
            api_key="", api_secret="", access_token="", access_token_secret=""
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_history_excludes_open_drafts(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """Every approve/reject route in this file commits durably (the route
    always calls db.commit()), so other tests' posted/rejected rows persist
    in this shared-DB test session — history is never provably empty. Assert
    identity instead: THIS still-open draft must not appear."""
    open_task = await _seed_draft(db_session)
    resp = await ceo_client.get("/api/video/posts/history")
    assert resp.status_code == HTTPStatus.OK
    ids = [row["task_id"] for row in resp.json()]
    assert str(open_task.id) not in ids


@pytest.mark.asyncio
async def test_history_respects_limit(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    for _ in range(3):
        t = await _seed_draft(db_session)
        with _LOCKED[0], _LOCKED[1]:
            await ceo_client.post(
                f"/api/video/posts/{t.id}/reject", json={"reason": "not relevant"}
            )
    resp = await ceo_client.get(
        "/api/video/posts/history", params={"limit": HISTORY_LIMIT}
    )
    assert resp.status_code == HTTPStatus.OK
    assert len(resp.json()) == HISTORY_LIMIT


@pytest.mark.asyncio
async def test_history_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/video/posts/history")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


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
    with _LOCKED[0], _LOCKED[1]:
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
    task = await _seed_draft(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        request_resp = await client.post(
            "/api/video/request",
            json={
                "occasion": "occ",
                "brief": "brief",
                "platforms": ["x"],
                "project_id": str(uuid4()),
            },
        )
        list_resp = await client.get("/api/video/posts")
        media_resp = await client.get(f"/api/video/posts/{task.id}/media?cut=vertical")
        creds_resp = await client.get("/api/tiktok/credentials")
    assert request_resp.status_code == HTTPStatus.FORBIDDEN
    assert list_resp.status_code == HTTPStatus.FORBIDDEN
    assert media_resp.status_code == HTTPStatus.FORBIDDEN
    assert creds_resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


# --- MinIO serve path (chunk 4) — unit-style, no DB / no real MinIO ------------
# These two tests monkeypatch ``get_task_service`` in the video routes module
# so they run without postgres (the ``db_session``-based tests above are
# skipped when Postgres is unreachable). Mocks only — no testcontainers.


def _stub_task_service_factory(task: object) -> object:
    """A ``get_task_service``-shaped stub (the real one is a sync factory
    returning a service with an async ``.get``). Patched in place of
    ``video_module.get_task_service`` so the route runs without postgres."""

    class _Svc:
        async def get(self, _task_id: UUID) -> object:
            return task

    return _Svc()


def _make_task(mp4_path: str, task_id: UUID) -> SimpleNamespace:
    """A minimal task-shaped stub carrying the video_draft marker the route
    reads — enough for the media route, no DB row needed."""
    return SimpleNamespace(
        id=task_id,
        source=VIDEO_POST_SOURCE,
        orchestration_markers={"video_draft": {"mp4_paths": {"vertical": mp4_path}}},
    )


def _patch_task_service(monkeypatch: pytest.MonkeyPatch, task: object) -> None:
    monkeypatch.setattr(
        video_module,
        "get_task_service",
        lambda _db: _stub_task_service_factory(task),
    )


def _minio_stream(_key: str) -> object:
    """Stub ``get_object_stream`` yielding fixed bytes for ``StreamingResponse``."""
    return iter([b"minio-stream-bytes"])


@pytest.mark.asyncio
async def test_media_serves_from_minio_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configured serve path: when MinIO is configured, the media route streams
    the object via ``minio_client.get_object_stream`` (key = basename) and the
    panel-preview URL/headers stay identical. ``_require_ceo`` still 403s a
    non-CEO agent. No DB / no real MinIO — ``get_task_service`` is stubbed so
    the route runs without postgres."""
    # A real local file so the route's is_file() + confinement checks pass.
    # The served bytes come from the stubbed MinIO stream below, NOT this
    # file — that's what proves the MinIO path was taken rather than the
    # FileResponse fallback.
    monkeypatch.setattr(cfg, "video_output_dir", str(tmp_path))
    vertical = tmp_path / "clip-vertical.mp4"
    vertical.write_bytes(b"local-file-bytes")
    task_id = uuid4()
    _patch_task_service(monkeypatch, _make_task(str(vertical), task_id))
    # non-None sentinel so the route takes the MinIO branch.
    monkeypatch.setattr(minio_client, "get_client", lambda: True)
    # The route probes stat_object eagerly before streaming; stub it to pass.
    monkeypatch.setattr(minio_client, "stat_object", lambda _key: None)
    monkeypatch.setattr(minio_client, "get_object_stream", _minio_stream)

    # CEO 200 — streamed from MinIO.
    app = _build_app(None, AgentRole.CEO, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/video/posts/{task_id}/media?cut=vertical")
        assert resp.status_code == HTTPStatus.OK
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.content == b"minio-stream-bytes"
    app.dependency_overrides.clear()

    # Non-CEO 403 — _require_ceo still gates end-to-end (no presigned URL).
    app = _build_app(None, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/video/posts/{task_id}/media?cut=vertical")
        assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_media_falls_back_to_local_file_when_minio_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unconfigured fallback: with ``get_client`` returning None
    (``minio_endpoint`` empty), the media route serves the local file via
    ``FileResponse`` — the body equals the local file's bytes. No DB / no
    real MinIO."""
    monkeypatch.setattr(cfg, "video_output_dir", str(tmp_path))
    vertical = tmp_path / "clip-vertical.mp4"
    vertical.write_bytes(b"local-file-bytes")
    task_id = uuid4()
    _patch_task_service(monkeypatch, _make_task(str(vertical), task_id))
    monkeypatch.setattr(minio_client, "get_client", lambda: None)

    app = _build_app(None, AgentRole.CEO, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/video/posts/{task_id}/media?cut=vertical")
        assert resp.status_code == HTTPStatus.OK
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.content == b"local-file-bytes"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_media_falls_back_to_local_file_when_minio_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3Error fallback: when MinIO is configured but the object is missing
    (NoSuchKey — an old render not yet in MinIO) or MinIO is down, the route's
    eager ``stat_object`` probe raises, the ``try/except`` catches it, and the
    route serves the local file via ``FileResponse``. ``get_object_stream`` is
    never called. No DB / no real MinIO."""
    monkeypatch.setattr(cfg, "video_output_dir", str(tmp_path))
    vertical = tmp_path / "clip-vertical.mp4"
    vertical.write_bytes(b"local-file-bytes")
    task_id = uuid4()
    _patch_task_service(monkeypatch, _make_task(str(vertical), task_id))
    monkeypatch.setattr(minio_client, "get_client", lambda: True)

    def _stat_raises(_key: str) -> None:
        raise RuntimeError("minio NoSuchKey / down")

    monkeypatch.setattr(minio_client, "stat_object", _stat_raises)

    # If the route wrongly takes the MinIO stream branch, this would be called
    # and the assertion below would fail — guard against a regression.
    def _stream_must_not_be_called(_key: str) -> object:
        pytest.fail("get_object_stream must not be called when stat_object raises")

    monkeypatch.setattr(minio_client, "get_object_stream", _stream_must_not_be_called)

    app = _build_app(None, AgentRole.CEO, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/video/posts/{task_id}/media?cut=vertical")
        assert resp.status_code == HTTPStatus.OK
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.content == b"local-file-bytes"  # FileResponse fallback, not MinIO
    app.dependency_overrides.clear()


# --- re-render (task 3, 2026-07-10) -------------------------------------------


@pytest.mark.asyncio
async def test_rerender_clears_state_and_returns_pipeline_item(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_authoring_task(
        db_session,
        status=TaskStatus.COMPLETED,
        draft_extra={
            "composition_id": "Intro",
            "render_status": "failed",
            "render_attempts": markers.MAX_VIDEO_RENDER_ATTEMPTS,
            "render_error": "sidecar timeout",
        },
    )
    resp = await ceo_client.post(f"/api/video/pipeline/{task.id}/rerender")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["task_id"] == str(task.id)
    assert body["render_status"] is None
    assert body["render_attempts"] == 0
    assert body["render_error"] is None
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert "render_status" not in draft
    assert "render_attempts" not in draft
    assert "render_error" not in draft
    assert draft["composition_id"] == "Intro"  # everything else preserved


@pytest.mark.asyncio
async def test_rerender_missing_task_is_404(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.post(f"/api/video/pipeline/{uuid4()}/rerender")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_rerender_non_completed_task_is_404(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_authoring_task(
        db_session,
        status=TaskStatus.IN_PROGRESS,
        draft_extra={"composition_id": "Intro"},
    )
    resp = await ceo_client.post(f"/api/video/pipeline/{task.id}/rerender")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_rerender_without_composition_id_is_404(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_authoring_task(db_session, status=TaskStatus.COMPLETED)
    resp = await ceo_client.post(f"/api/video/pipeline/{task.id}/rerender")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_rerender_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    task = await _seed_authoring_task(
        db_session,
        status=TaskStatus.COMPLETED,
        draft_extra={"composition_id": "Intro"},
    )
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/video/pipeline/{task.id}/rerender")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


# --- preview proxy (task 3, 2026-07-10) ---------------------------------------


def _fake_workspace_service(root: Path) -> object:
    """A ``get_workspace_service``-shaped stub whose ``ensure_read_clone``
    returns a fixed local dir — no real git clone touched."""

    class _Svc:
        async def ensure_read_clone(self, _slug: str, *, force: bool = False) -> Path:
            _ = force
            return root

    return _Svc()


def test_resolve_preview_path_serves_file_inside_root(tmp_path: Path) -> None:
    root = (tmp_path / "clone").resolve()
    (root / "motion" / "compositions" / "Intro").mkdir(parents=True)
    target = root / "motion" / "compositions" / "Intro" / "vertical.html"
    target.write_text("<html></html>")
    resolved = video_module._resolve_preview_path(
        root, "motion/compositions/Intro/vertical.html"
    )
    assert resolved == target.resolve()


def test_resolve_preview_path_blocks_dot_dot_traversal(tmp_path: Path) -> None:
    root = (tmp_path / "clone").resolve()
    (root / "motion").mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    assert video_module._resolve_preview_path(root, "../secret.txt") is None
    assert video_module._resolve_preview_path(root, "motion/../../secret.txt") is None


def test_resolve_preview_path_blocks_absolute_path_override(tmp_path: Path) -> None:
    """A leading '/' in file_path would otherwise let pathlib's ``/``
    operator discard ``root`` entirely and resolve straight to the absolute
    path — the ``lstrip("/")`` guard neutralizes that."""
    root = (tmp_path / "clone").resolve()
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("nope")
    assert video_module._resolve_preview_path(root, str(outside)) is None


def test_resolve_preview_path_missing_file_is_none(tmp_path: Path) -> None:
    root = (tmp_path / "clone").resolve()
    root.mkdir()
    assert video_module._resolve_preview_path(root, "motion/nope.html") is None


@pytest.mark.asyncio
async def test_preview_serves_composition_html_and_sibling_kit_asset(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comp_dir = tmp_path / "motion" / "compositions" / "Intro"
    comp_dir.mkdir(parents=True)
    (comp_dir / "vertical.html").write_text("<html>intro</html>")
    kit_dir = tmp_path / "motion" / "kit"
    kit_dir.mkdir(parents=True)
    (kit_dir / "kit.css").write_text("body{}")
    monkeypatch.setattr(
        video_module,
        "get_workspace_service",
        lambda _db: _fake_workspace_service(tmp_path),
    )
    task = await _seed_authoring_task(
        db_session,
        status=TaskStatus.IN_PROGRESS,
        draft_extra={"composition_id": "Intro"},
    )
    resp = await ceo_client.get(
        f"/api/video/preview/{task.id}/motion/compositions/Intro/vertical.html"
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.text == "<html>intro</html>"
    assert resp.headers.get("x-frame-options") == "SAMEORIGIN"
    assert "frame-ancestors" in resp.headers.get("content-security-policy", "")

    kit_resp = await ceo_client.get(f"/api/video/preview/{task.id}/motion/kit/kit.css")
    assert kit_resp.status_code == HTTPStatus.OK
    assert kit_resp.text == "body{}"


@pytest.mark.asyncio
async def test_preview_missing_file_is_404(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "motion").mkdir()
    monkeypatch.setattr(
        video_module,
        "get_workspace_service",
        lambda _db: _fake_workspace_service(tmp_path),
    )
    task = await _seed_authoring_task(
        db_session,
        status=TaskStatus.IN_PROGRESS,
        draft_extra={"composition_id": "Intro"},
    )
    resp = await ceo_client.get(
        f"/api/video/preview/{task.id}/motion/compositions/Intro/vertical.html"
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_preview_missing_task_is_404(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get(f"/api/video/preview/{uuid4()}/vertical.html")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_preview_non_video_task_is_404(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)  # source=video_post, not video
    resp = await ceo_client.get(f"/api/video/preview/{task.id}/vertical.html")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_preview_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    task = await _seed_authoring_task(
        db_session,
        status=TaskStatus.IN_PROGRESS,
        draft_extra={"composition_id": "Intro"},
    )
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/video/preview/{task.id}/vertical.html")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


# --- preview frames (CEO-facing request_render surface) -----------------------


def _write_preview_frame(
    root: Path, orientation: str, idx: int, count: int, timestamp: float
) -> Path:
    """One request_render-shaped frame file — filename encodes index/count/
    timestamp exactly as video-renderer/render.js writes it."""
    d = root / orientation
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"frame-{idx:02d}-of-{count}-at-{timestamp:.1f}s.png"
    path.write_bytes(b"fake-png-bytes")
    return path


def _previews_dir(workspaces_root: Path, project_slug: str, task_id: UUID) -> Path:
    return workspaces_root / project_slug / ".previews" / task_id.hex[:8]


@pytest.mark.asyncio
async def test_preview_frames_lists_both_orientations_with_marker_metadata(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "workspaces_root", str(tmp_path))
    task = await _seed_authoring_task(
        db_session,
        status=TaskStatus.IN_PROGRESS,
        draft_extra={"composition_id": "Intro"},
    )
    project = await db_session.get(ProjectTable, task.project_id)
    assert project is not None
    root = _previews_dir(tmp_path, project.slug, cast("UUID", task.id))
    _write_preview_frame(root, "vertical", 1, 2, 1.5)
    _write_preview_frame(root, "vertical", 2, 2, 4.5)
    _write_preview_frame(root, "square", 1, 1, 3.0)
    markers.set_render_preview(
        task,
        {
            "at": "2026-07-20T00:00:00+00:00",
            "composition_id": "Intro",
            "orientation": "square",
            "frame_count": 1,
            "duration_seconds": PREVIEW_DURATION_SECONDS,
            "frames": [],
            "head_sha": "abc123",
            "dirty": False,
        },
    )
    await db_session.flush()

    resp = await ceo_client.get(f"/api/video/preview-frames/{task.id}")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["composition_id"] == "Intro"
    assert body["duration_seconds"] == PREVIEW_DURATION_SECONDS
    assert body["head_sha"] == "abc123"
    assert body["dirty"] is False
    assert body["rendered_at"] == "2026-07-20T00:00:00+00:00"
    vertical = body["frames"]["vertical"]
    assert [f["index"] for f in vertical] == [1, 2]
    assert [f["timestamp_seconds"] for f in vertical] == [1.5, 4.5]
    assert body["frames"]["square"][0]["file"].startswith("frame-01-of-1-at-3.0s")


@pytest.mark.asyncio
async def test_preview_frames_no_render_yet_is_404(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source=video task with no request_render call yet has nothing under
    .previews/ — 404, not an empty 200 the panel would render as a blank
    section."""
    monkeypatch.setattr(cfg, "workspaces_root", str(tmp_path))
    task = await _seed_authoring_task(db_session, status=TaskStatus.IN_PROGRESS)
    resp = await ceo_client.get(f"/api/video/preview-frames/{task.id}")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_preview_frames_missing_task_is_404(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get(f"/api/video/preview-frames/{uuid4()}")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_preview_frames_non_video_task_is_404(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)  # source=video_post, not video
    resp = await ceo_client.get(f"/api/video/preview-frames/{task.id}")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_preview_frames_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    task = await _seed_authoring_task(db_session, status=TaskStatus.IN_PROGRESS)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/video/preview-frames/{task.id}")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_preview_frame_streams_png_bytes(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "workspaces_root", str(tmp_path))
    task = await _seed_authoring_task(db_session, status=TaskStatus.IN_PROGRESS)
    project = await db_session.get(ProjectTable, task.project_id)
    assert project is not None
    root = _previews_dir(tmp_path, project.slug, cast("UUID", task.id))
    frame_path = _write_preview_frame(root, "vertical", 1, 1, 0.5)
    resp = await ceo_client.get(
        f"/api/video/preview-frames/{task.id}/vertical/{frame_path.name}"
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"fake-png-bytes"


@pytest.mark.asyncio
async def test_preview_frame_bad_orientation_is_400(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_authoring_task(db_session, status=TaskStatus.IN_PROGRESS)
    resp = await ceo_client.get(
        f"/api/video/preview-frames/{task.id}/diagonal/frame-01-of-1-at-0.5s.png"
    )
    assert resp.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_preview_frame_missing_file_is_404(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "workspaces_root", str(tmp_path))
    task = await _seed_authoring_task(db_session, status=TaskStatus.IN_PROGRESS)
    resp = await ceo_client.get(
        f"/api/video/preview-frames/{task.id}/vertical/frame-01-of-1-at-0.5s.png"
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_preview_frame_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    task = await _seed_authoring_task(db_session, status=TaskStatus.IN_PROGRESS)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/video/preview-frames/{task.id}/vertical/frame-01-of-1-at-0.5s.png"
        )
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


def test_resolve_preview_path_confines_frame_orientation_traversal(
    tmp_path: Path,
) -> None:
    """The preview-frame route composes ``f"{orientation}/{filename}"`` before
    calling ``_resolve_preview_path`` — same confinement the composition-HTML
    proxy uses, applied to a task's .previews/ dir instead of its read-clone."""
    root = (tmp_path / "roboco-x" / ".previews" / "abcd1234").resolve()
    (root / "vertical").mkdir(parents=True)
    frame = root / "vertical" / "frame-01-of-1-at-0.5s.png"
    frame.write_bytes(b"png")
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"nope")
    assert (
        video_module._resolve_preview_path(root, "vertical/frame-01-of-1-at-0.5s.png")
        == frame.resolve()
    )
    assert video_module._resolve_preview_path(root, "vertical/../../secret.png") is None
    assert video_module._resolve_preview_path(root, "../secret.png") is None
