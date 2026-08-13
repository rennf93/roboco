"""The orchestrator video-render loop: dormant when off; the cycle wrapper
resolves ids then delegates per-task (mocked wiring test, mirrors
test_dep_update_loop.py); the per-task render (mocked renderer/workspace,
real DB) renders both cuts, holds one video_post draft, and is idempotent (a
rendered task is never re-rendered; a failed render bounded-retries up to a
cap, then is terminal).

2026-08 pool-exhaustion hardening: ``_render_video_task`` now runs in THREE
separate short DB sessions (resolve / render-project-lookup / write) instead
of one session held across the whole task, so no pool connection sits
checked out across the render sidecar's HTTP calls (tens to hundreds of
seconds in production). The real-DB tests below patch ``get_db_context`` to
a no-commit stand-in bound to the shared ``db_session`` fixture: every
phase lands on the SAME test transaction (never committing, so it never
pollutes the session-scoped shared test database the way routing it through
a REAL committing session would), while a dedicated fully-mocked test
(``test_render_video_task_holds_no_session_across_render_calls``) pins the
actual connection-release behavior a no-commit stand-in can't observe.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus as TS
from roboco.runtime.orchestrator import (
    _MAX_VIDEO_RENDER_ATTEMPTS,
    AgentOrchestrator,
)
from roboco.services import video_engine as video_engine_module
from roboco.services.task import VIDEO_POST_SOURCE, get_task_service
from roboco.services.video_engine import VideoEngine
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SECRETARY_UUID = _foundation.AGENTS["secretary-1"].uuid
UX_DEV_1_UUID = _foundation.AGENTS["ux-dev-1"].uuid
UX_DEV_2_UUID = _foundation.AGENTS["ux-dev-2"].uuid
SLUG = "roboco"
ONE = 1
TWO = 2
FOUR = 4


def _orch() -> Any:
    return AgentOrchestrator.__new__(AgentOrchestrator)


class _AlwaysAcquiredMutex:
    """Stand-in for ``HeartbeatMutex``: always acquires immediately, no live
    Redis required (matches the project's ``_no_live_redis`` fixture and
    mirrors ``test_video_engine.py``'s identical stub)."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def acquire(self) -> str | None:
        return "tok"

    async def release(self, _token: str) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_occasion_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(video_engine_module, "HeartbeatMutex", _AlwaysAcquiredMutex)


class _FakeRenderer:
    """Records every render() call; returns a deterministic path or raises."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail = fail

    async def render(
        self,
        *,
        source_dir: str,
        composition_id: str,
        input_props: dict[str, Any],
        orientation: str,
        render_key: str,
    ) -> str:
        _ = input_props
        self.calls.append(
            {
                "source_dir": source_dir,
                "composition_id": composition_id,
                "orientation": orientation,
                "render_key": render_key,
            }
        )
        if self.fail:
            raise RuntimeError("render blew up")
        return f"/fake-out/{composition_id}-{orientation}.mp4"


def _db_ctx(db: Any) -> Any:
    @asynccontextmanager
    async def _ctx(**_kwargs: str) -> Any:
        yield db

    return _ctx


async def _seed(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (SECRETARY_UUID, "secretary-1", AgentRole.SECRETARY, None),
        (UX_DEV_1_UUID, "ux-dev-1", AgentRole.DEVELOPER, Team.UX_UI),
        (UX_DEV_2_UUID, "ux-dev-2", AgentRole.DEVELOPER, Team.UX_UI),
    ):
        if await session.get(AgentTable, uuid) is None:
            session.add(
                AgentTable(
                    id=uuid,
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


def _enable(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    monkeypatch.setattr(cfg, "video_engine_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    monkeypatch.setattr(cfg, "video_max_open_posts", 5)
    monkeypatch.setattr(cfg, "video_render_interval_seconds", 120.0)
    for key, value in overrides.items():
        monkeypatch.setattr(cfg, key, value)


async def _make_completed_video_task(
    session: AsyncSession, *, occasion: str, composition_id: str | None
) -> Any:
    engine = VideoEngine(session)
    task = await engine.open_video_task(
        occasion=occasion,
        script="Here's what shipped",
        platforms=["x", "tiktok"],
        brief="Announce the release",
    )
    assert task is not None
    if composition_id is not None:
        draft = markers.get_video_draft(task) or {}
        markers.set_video_draft(
            task,
            {
                **draft,
                "composition_id": composition_id,
                "input_props": {"title": "hello"},
                "x_caption": "Check out our new release!",
                "tiktok_caption": "New release, check it out",
                "platforms": ["x", "tiktok"],
            },
        )
    task.status = TS.COMPLETED
    await session.flush()
    return task


def _render_patches(renderer: _FakeRenderer, workspace: Any, db: Any) -> Any:
    """The 3 patches ``_render_video_task``'s three internal sessions need:
    the renderer + workspace I/O boundary, and ``get_db_context`` routed to
    the shared (never-committing) test session."""
    return (
        patch(
            "roboco.services.video_renderer_client.get_video_renderer",
            lambda: renderer,
        ),
        patch(
            "roboco.services.workspace.get_workspace_service",
            lambda _db: workspace,
        ),
        patch("roboco.db.get_db_context", _db_ctx(db)),
    )


def _fake_workspace() -> Any:
    return SimpleNamespace(
        ensure_read_clone=AsyncMock(return_value=Path("/fake-clone"))
    )


# --------------------------------------------------------------------------- #
# _video_render_loop dormancy
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_loop_returns_immediately_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "video_engine_enabled", False)
    stub = cast("AgentOrchestrator", SimpleNamespace(_running=True))
    await asyncio.wait_for(AgentOrchestrator._video_render_loop(stub), timeout=1.0)


# --------------------------------------------------------------------------- #
# _run_video_render_cycle — wiring only (mocked db/service, mirrors
# test_dep_update_loop.py); the substantive render behavior is covered below
# against _render_video_task directly.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_cycle_delegates_one_call_per_completed_task_id() -> None:
    orch = _orch()
    task_a = MagicMock()
    task_b = MagicMock()
    db = MagicMock()
    task_svc = MagicMock()
    task_svc.list_completed_video_tasks = AsyncMock(return_value=[task_a, task_b])
    orch._render_video_task = AsyncMock()
    with (
        patch("roboco.db.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.get_task_service", return_value=task_svc),
        patch(
            "roboco.services.maintenance_pause.is_paused",
            AsyncMock(return_value=False),
        ),
    ):
        await orch._run_video_render_cycle()
    # One call per completed task's OWN id, never the task object, and
    # never a shared db passed alongside it (each task owns its own
    # sessions internally).
    assert orch._render_video_task.await_args_list == [
        call(task_a.id),
        call(task_b.id),
    ]


@pytest.mark.asyncio
async def test_run_cycle_with_no_completed_tasks_calls_nothing() -> None:
    orch = _orch()
    db = MagicMock()
    task_svc = MagicMock()
    task_svc.list_completed_video_tasks = AsyncMock(return_value=[])
    orch._render_video_task = AsyncMock()
    with (
        patch("roboco.db.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.get_task_service", return_value=task_svc),
        patch(
            "roboco.services.maintenance_pause.is_paused",
            AsyncMock(return_value=False),
        ),
    ):
        await orch._run_video_render_cycle()
    orch._render_video_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_cycle_a_raise_stops_later_ids_but_prior_ids_already_ran() -> None:
    """Each id is fully processed (its own sessions, its own commits) BEFORE
    the next id is even touched, and no shared session spans the cycle, so a
    raise on one id can only ever stop LATER ids in the same tick (retried
    next interval by ``_video_render_loop``); it can't roll back or block
    ids that already ran.
    """
    orch = _orch()
    task_a = MagicMock()
    task_b = MagicMock()
    task_c = MagicMock()
    db = MagicMock()
    task_svc = MagicMock()
    task_svc.list_completed_video_tasks = AsyncMock(
        return_value=[task_a, task_b, task_c]
    )
    seen: list[Any] = []

    async def _render(task_id: Any) -> None:
        seen.append(task_id)
        if task_id is task_b.id:
            raise RuntimeError("B blew up")

    orch._render_video_task = AsyncMock(side_effect=_render)
    with (
        patch("roboco.db.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.get_task_service", return_value=task_svc),
        patch(
            "roboco.services.maintenance_pause.is_paused",
            AsyncMock(return_value=False),
        ),
        pytest.raises(RuntimeError, match="B blew up"),
    ):
        await orch._run_video_render_cycle()
    assert seen == [task_a.id, task_b.id]  # C never reached


# --------------------------------------------------------------------------- #
# The pool-release fix itself: no DB session may be held across the render
# sidecar's HTTP calls. A no-commit db_session stand-in (used by every real-
# DB test below) can't observe a real release, so this uses full mocks that
# track whether a get_db_context() block is currently open.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_render_video_task_holds_no_session_across_render_calls() -> None:
    """2026-07-29 pool-exhaustion regression: fails against the pre-fix
    shape, where one session spanned the whole task's render (both
    renderer.render() calls happened with a connection still checked out)."""
    orch = _orch()
    task_id = uuid4()
    project_id = uuid4()

    resolve_task = SimpleNamespace(
        id=task_id,
        project_id=project_id,
        orchestration_markers={
            "video_draft": {"composition_id": "Intro", "input_props": {}}
        },
    )
    write_task = SimpleNamespace(
        id=task_id,
        project_id=project_id,
        orchestration_markers=dict(resolve_task.orchestration_markers),
    )
    project = SimpleNamespace(id=project_id, slug=SLUG)

    open_sessions = 0

    @asynccontextmanager
    async def _fake_db_ctx(**_kwargs: str) -> Any:
        nonlocal open_sessions
        open_sessions += 1
        db = MagicMock()
        try:
            yield db
        finally:
            open_sessions -= 1

    task_svc = MagicMock()
    task_svc.get = AsyncMock(side_effect=[resolve_task, write_task])
    project_svc = MagicMock()
    project_svc.get = AsyncMock(return_value=project)
    workspace = _fake_workspace()
    video_engine = MagicMock()
    video_engine._originate_video_post = AsyncMock()

    session_open_at_render: list[bool] = []

    async def _render(**kwargs: Any) -> str:
        session_open_at_render.append(open_sessions > 0)
        return f"/fake-out/{kwargs['composition_id']}-{kwargs['orientation']}.mp4"

    renderer = SimpleNamespace(render=AsyncMock(side_effect=_render))

    with (
        patch("roboco.db.get_db_context", _fake_db_ctx),
        patch("roboco.services.task.get_task_service", return_value=task_svc),
        patch("roboco.services.project.get_project_service", return_value=project_svc),
        patch("roboco.services.workspace.get_workspace_service", lambda _db: workspace),
        patch(
            "roboco.services.video_renderer_client.get_video_renderer",
            lambda: renderer,
        ),
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=video_engine,
        ),
    ):
        await orch._render_video_task(task_id)

    # No get_db_context() block was open during either render() call.
    assert session_open_at_render == [False, False]
    assert renderer.render.await_count == TWO
    video_engine._originate_video_post.assert_awaited_once()
    # Nothing left open behind us.
    assert open_sessions == 0


# --------------------------------------------------------------------------- #
# _render_video_task — real DB (flush only, never commits: the session-scoped
# shared test DB stays clean via this test's own rollback teardown, since
# get_db_context is patched to a no-commit stand-in bound to db_session),
# mocked renderer + workspace clone.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_render_video_task_renders_both_cuts_and_materializes_post(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    task = await _make_completed_video_task(
        db_session, occasion="render-both-cuts", composition_id="Intro"
    )

    renderer = _FakeRenderer()
    workspace = _fake_workspace()
    orch = _orch()
    p1, p2, p3 = _render_patches(renderer, workspace, db_session)
    with p1, p2, p3:
        await orch._render_video_task(task.id)

    workspace.ensure_read_clone.assert_awaited_once_with(SLUG)
    assert len(renderer.calls) == TWO
    orientations = {c["orientation"] for c in renderer.calls}
    assert orientations == {"vertical", "square"}
    expected_motion_dir = str(Path("/fake-clone") / "motion")
    assert all(c["source_dir"] == expected_motion_dir for c in renderer.calls)
    assert all(c["render_key"] == str(task.id) for c in renderer.calls)  # task-scoped

    posts = await get_task_service(db_session).list_open_video_posts()
    assert len(posts) == ONE
    assert posts[0].source == VIDEO_POST_SOURCE
    draft = markers.get_video_draft(posts[0])
    assert draft is not None
    assert draft["mp4_paths"] == {
        "vertical": "/fake-out/Intro-vertical.mp4",
        "square": "/fake-out/Intro-square.mp4",
    }
    assert draft["x_caption"] == "Check out our new release!"
    assert draft["tiktok_caption"] == "New release, check it out"

    source_draft = markers.get_video_draft(task)
    assert source_draft is not None
    assert source_draft["render_status"] == "rendered"


@pytest.mark.asyncio
async def test_render_video_task_resolves_workspace_from_task_project_not_settings(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The render loop resolves the read-clone from the authoring task's OWN
    project_id — flipping self_heal_project_slug to a bogus value AFTER the
    task was authored must not affect the render, proving the loop no longer
    reads that setting live."""
    await _seed(db_session)
    _enable(monkeypatch)
    task = await _make_completed_video_task(
        db_session, occasion="own-project-not-settings", composition_id="Intro"
    )
    monkeypatch.setattr(cfg, "self_heal_project_slug", "no-such-project-anymore")

    renderer = _FakeRenderer()
    workspace = _fake_workspace()
    orch = _orch()
    p1, p2, p3 = _render_patches(renderer, workspace, db_session)
    with p1, p2, p3:
        await orch._render_video_task(task.id)

    # Still resolved via the task's own project_id -> slug "roboco", not the
    # now-bogus self_heal_project_slug.
    workspace.ensure_read_clone.assert_awaited_once_with(SLUG)
    posts = await get_task_service(db_session).list_open_video_posts()
    assert len(posts) == ONE


@pytest.mark.asyncio
async def test_render_video_task_second_call_is_idempotent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    task = await _make_completed_video_task(
        db_session, occasion="idempotent", composition_id="Intro"
    )

    renderer = _FakeRenderer()
    workspace = _fake_workspace()
    orch = _orch()
    p1, p2, p3 = _render_patches(renderer, workspace, db_session)
    with p1, p2, p3:
        await orch._render_video_task(task.id)
        await orch._render_video_task(task.id)  # must be a no-op

    assert len(renderer.calls) == TWO  # not four — the second call skipped it
    posts = await get_task_service(db_session).list_open_video_posts()
    assert len(posts) == ONE


@pytest.mark.asyncio
async def test_rerender_clears_state_so_next_cycle_re_renders(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CEO re-render flow end to end: a rendered task is a no-op on a second
    render pass (idempotent); clearing render_status/render_attempts via
    VideoEngine.rerender makes the NEXT pass pick it up and render it again."""
    await _seed(db_session)
    _enable(monkeypatch)
    task = await _make_completed_video_task(
        db_session, occasion="rerender-cycle", composition_id="Intro"
    )

    renderer = _FakeRenderer()
    workspace = _fake_workspace()
    orch = _orch()
    p1, p2, p3 = _render_patches(renderer, workspace, db_session)
    with p1, p2, p3:
        await orch._render_video_task(task.id)
    assert len(renderer.calls) == TWO
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["render_status"] == "rendered"

    rerendered = await VideoEngine(db_session).rerender(task.id)
    assert rerendered is not None
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert "render_status" not in draft
    assert "render_attempts" not in draft

    with p1, p2, p3:
        await orch._render_video_task(task.id)  # re-picked up
    assert len(renderer.calls) == FOUR  # rendered a second time, not skipped
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["render_status"] == "rendered"


@pytest.mark.asyncio
async def test_render_video_task_skips_task_without_composition_id(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    task = await _make_completed_video_task(
        db_session, occasion="no-composition", composition_id=None
    )

    renderer = _FakeRenderer()
    workspace = _fake_workspace()
    orch = _orch()
    p1, p2, p3 = _render_patches(renderer, workspace, db_session)
    with p1, p2, p3:
        await orch._render_video_task(task.id)

    assert renderer.calls == []
    workspace.ensure_read_clone.assert_not_awaited()
    posts = await get_task_service(db_session).list_open_video_posts()
    assert posts == []
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft.get("render_status") is None


@pytest.mark.asyncio
async def test_render_video_task_single_failure_retries_not_terminal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failure bumps the attempt counter but does NOT terminally fail the
    task — a stale read-clone or a transient sidecar blip must be retried on a
    later cycle, not silently lost."""
    await _seed(db_session)
    _enable(monkeypatch)
    task = await _make_completed_video_task(
        db_session, occasion="render-fails-once", composition_id="Intro"
    )

    renderer = _FakeRenderer(fail=True)
    workspace = _fake_workspace()
    orch = _orch()
    p1, p2, p3 = _render_patches(renderer, workspace, db_session)
    with p1, p2, p3:
        await orch._render_video_task(task.id)

    posts = await get_task_service(db_session).list_open_video_posts()
    assert posts == []
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["render_attempts"] == ONE
    assert draft.get("render_status") is None  # retried next cycle, not terminal


@pytest.mark.asyncio
async def test_render_video_task_terminal_after_max_attempts(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After _MAX_VIDEO_RENDER_ATTEMPTS failures the task is terminally failed
    and never rendered again — a genuinely broken composition can't loop."""
    await _seed(db_session)
    _enable(monkeypatch)
    task = await _make_completed_video_task(
        db_session, occasion="max-attempts", composition_id="Intro"
    )
    seeded = markers.get_video_draft(task) or {}
    markers.set_video_draft(
        task, {**seeded, "render_attempts": _MAX_VIDEO_RENDER_ATTEMPTS - 1}
    )
    await db_session.flush()

    renderer = _FakeRenderer(fail=True)
    workspace = _fake_workspace()
    orch = _orch()
    p1, p2, p3 = _render_patches(renderer, workspace, db_session)
    notify_svc = AsyncMock()
    with (
        p1,
        p2,
        p3,
        patch(
            "roboco.services.notification.NotificationService",
            return_value=notify_svc,
        ),
    ):
        await orch._render_video_task(task.id)  # tips to terminal
        calls_at_terminal = len(renderer.calls)
        await orch._render_video_task(task.id)  # now a no-op

    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["render_attempts"] == _MAX_VIDEO_RENDER_ATTEMPTS
    assert draft["render_status"] == "failed"
    assert len(renderer.calls) == calls_at_terminal  # not retried after terminal
    posts = await get_task_service(db_session).list_open_video_posts()
    assert posts == []
    # Exactly one CEO alert — the second (no-op) call must not re-notify.
    notify_svc.send_ack_notification.assert_awaited_once()
    notify_kwargs = notify_svc.send_ack_notification.await_args.kwargs
    assert notify_kwargs["to_agent"] == "ceo"
    assert task.title in notify_kwargs["body"]
    assert "render blew up" in notify_kwargs["body"]
    assert notify_kwargs["task_id"] == task.id


@pytest.mark.asyncio
async def test_render_video_task_notify_failure_does_not_raise(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken notification path (e.g. the second DB connection is down)
    must not surface out of the render loop — best-effort, like the
    strategy-engine failure notifier."""
    await _seed(db_session)
    _enable(monkeypatch)
    task = await _make_completed_video_task(
        db_session, occasion="notify-fails", composition_id="Intro"
    )
    seeded = markers.get_video_draft(task) or {}
    markers.set_video_draft(
        task, {**seeded, "render_attempts": _MAX_VIDEO_RENDER_ATTEMPTS - 1}
    )
    await db_session.flush()

    renderer = _FakeRenderer(fail=True)
    workspace = _fake_workspace()
    orch = _orch()
    p1, p2, p3 = _render_patches(renderer, workspace, db_session)
    with (
        p1,
        p2,
        p3,
        patch(
            "roboco.services.notification.NotificationService",
            side_effect=RuntimeError("notification DB unreachable"),
        ),
    ):
        await orch._render_video_task(task.id)  # must not raise

    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["render_status"] == "failed"
