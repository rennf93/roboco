"""The orchestrator video-render loop: dormant when off; the cycle wrapper
iterates + commits (mocked wiring test, mirrors test_dep_update_loop.py); the
per-task render (mocked renderer/workspace, real DB) renders both cuts, holds
one video_post draft, and is idempotent (a rendered task is never re-rendered;
a failed render bounded-retries up to a cap, then is terminal) — never itself
committing, so it never pollutes the session-scoped
shared test database the way routing it through the committing cycle wrapper
would.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, call, patch

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
    async def _ctx() -> Any:
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


def _render_patches(renderer: _FakeRenderer, workspace: Any) -> Any:
    return (
        patch(
            "roboco.services.video_renderer_client.get_video_renderer",
            lambda: renderer,
        ),
        patch(
            "roboco.services.workspace.get_workspace_service",
            lambda _db: workspace,
        ),
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
# against _render_video_task directly, which never commits.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_cycle_commits_per_task() -> None:
    orch = _orch()
    task_a = MagicMock()
    task_b = MagicMock()
    db = MagicMock()
    db.commit = AsyncMock()
    task_svc = MagicMock()
    task_svc.list_completed_video_tasks = AsyncMock(return_value=[task_a, task_b])
    orch._render_video_task = AsyncMock()
    with (
        patch("roboco.db.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.get_task_service", return_value=task_svc),
    ):
        await orch._run_video_render_cycle()
    assert orch._render_video_task.await_args_list == [
        call(db, task_a),
        call(db, task_b),
    ]
    # one commit per task — never one trailing commit after the loop
    assert db.commit.await_count == TWO


@pytest.mark.asyncio
async def test_run_cycle_with_no_completed_tasks_does_not_commit() -> None:
    orch = _orch()
    db = MagicMock()
    db.commit = AsyncMock()
    task_svc = MagicMock()
    task_svc.list_completed_video_tasks = AsyncMock(return_value=[])
    orch._render_video_task = AsyncMock()
    with (
        patch("roboco.db.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.get_task_service", return_value=task_svc),
    ):
        await orch._run_video_render_cycle()
    orch._render_video_task.assert_not_awaited()
    db.commit.assert_not_awaited()  # nothing rendered → nothing to durably persist


@pytest.mark.asyncio
async def test_run_cycle_commits_before_mid_cycle_raise_so_prior_render_durable() -> (
    None
):
    """A raise mid-cycle must not roll back prior renders: each render is
    committed before the next is attempted, so the committed
    render_status='rendered' is the idempotency key the next scan skips
    (instead of re-rendering + re-originating a second held video_post draft).
    """
    orch = _orch()
    task_a = MagicMock()
    task_b = MagicMock()
    db = MagicMock()
    db.commit = AsyncMock()
    task_svc = MagicMock()
    task_svc.list_completed_video_tasks = AsyncMock(return_value=[task_a, task_b])

    async def _render(_db: Any, task: Any) -> None:
        if task is task_b:
            raise RuntimeError("B blew up")

    orch._render_video_task = AsyncMock(side_effect=_render)
    with (
        patch("roboco.db.get_db_context", _db_ctx(db)),
        patch("roboco.services.task.get_task_service", return_value=task_svc),
        pytest.raises(RuntimeError, match="B blew up"),
    ):
        await orch._run_video_render_cycle()
    # A's commit happened BEFORE B raised — exactly one commit, A is durable
    db.commit.assert_awaited_once()
    assert orch._render_video_task.await_args_list == [
        call(db, task_a),
        call(db, task_b),
    ]


# --------------------------------------------------------------------------- #
# _render_video_task — real DB (flush only, never commits: the session-scoped
# shared test DB stays clean via this test's own rollback teardown), mocked
# renderer + workspace clone.
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
    p1, p2 = _render_patches(renderer, workspace)
    with p1, p2:
        await orch._render_video_task(db_session, task)

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
    p1, p2 = _render_patches(renderer, workspace)
    with p1, p2:
        await orch._render_video_task(db_session, task)

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
    p1, p2 = _render_patches(renderer, workspace)
    with p1, p2:
        await orch._render_video_task(db_session, task)
        await orch._render_video_task(db_session, task)  # must be a no-op

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
    p1, p2 = _render_patches(renderer, workspace)
    with p1, p2:
        await orch._render_video_task(db_session, task)
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

    with p1, p2:
        await orch._render_video_task(db_session, task)  # re-picked up
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
    p1, p2 = _render_patches(renderer, workspace)
    with p1, p2:
        await orch._render_video_task(db_session, task)

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
    p1, p2 = _render_patches(renderer, workspace)
    with p1, p2:
        await orch._render_video_task(db_session, task)

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
    p1, p2 = _render_patches(renderer, workspace)
    notify_svc = AsyncMock()
    with (
        p1,
        p2,
        patch(
            "roboco.services.notification.NotificationService",
            return_value=notify_svc,
        ),
    ):
        await orch._render_video_task(db_session, task)  # tips to terminal
        calls_at_terminal = len(renderer.calls)
        await orch._render_video_task(db_session, task)  # now a no-op

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
    p1, p2 = _render_patches(renderer, workspace)
    with (
        p1,
        p2,
        patch(
            "roboco.services.notification.NotificationService",
            side_effect=RuntimeError("notification DB unreachable"),
        ),
    ):
        await orch._render_video_task(db_session, task)  # must not raise

    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["render_status"] == "failed"
