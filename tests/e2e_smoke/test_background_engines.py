"""Background-engine smoke scenarios for the 0.19.0 scan batch.

Cross-layer wiring for the four findings the brief deferred here (the rest
are unit-covered):

- H24 — ``ReleaseExecutor.wait_for_ci`` polls through the window on a
  non-success (a failed first attempt while a re-run is still in_progress).
  Drives the real ``_GitReleaseOps.wait_for_ci`` with a stubbed
  ``get_git_service`` whose CI-conclusion returns ``failure`` on the first
  poll then ``success`` on the second; ``_CI_POLL_INTERVAL_SECONDS`` is
  patched to 0 so the poll is instant. A pre-fix ``return False`` on the
  non-success would fail this (it would never reach the success poll).
- H25 — ``sweep_orphan_release_locks`` deletes a release-proposal mutex
  whose owner isn't in the in-flight registry, preserves an in-flight one.
  Mirrors ``tests/unit/services/test_release_proposal_orphan_sweep.py`` but
  drives the real sweep against the real e2e Redis (db 15, test-isolated),
  not a fake. Skips when no local Redis is reachable (the e2e-smoke make
  target starts one, but a bare ``uv run pytest`` may not).
- M1 — ``LiveTikTokPoster._refresh`` commits the rotated tokens in an
  independent session, so a lock-loss rollback of the caller's session does
  not discard them. Seeds a ``tiktok_credentials`` row, drives the real
  ``_refresh`` with a mocked TikTok token endpoint, rolls the caller session
  back, and re-reads from a fresh session — the rotated tokens must persist.
- M21 — ``_run_video_render_cycle`` commits per-task, so a raise mid-cycle
  does not roll back prior renders. Drives the real cycle against the e2e
  DB with a stubbed ``_render_video_task`` (task A materializes the held
  video_post draft via the real ``_materialize_video_post``; task B raises);
  asserts A's ``render_status='rendered'`` + A's video_post draft are
  durable in a fresh session (committed before B raised). Mirrors the unit
  test in ``tests/unit/runtime/test_video_render_loop.py`` but drives the
  real ``get_db_context`` + real DB instead of a mock session.

M11 (engine-loop liveness watchdog) is NOT exercised here — a non-flaky
liveness-alert harness needs a controllable clock + a long-running loop
tick, which the in-process e2e stack (no background loops started) can't
model without flakiness. M11 is unit-covered by
``tests/unit/runtime/test_loop_liveness_watchdog.py``.

Deviations from a true end-to-end exercise (noted): H24's CI-conclusion
source is stubbed (the e2e harness has no real GitHub CI); H25 uses the
real e2e Redis but skips when unreachable; M1's TikTok token endpoint is
mocked (no real TikTok egress); M21's ``_render_video_task`` is stubbed
(the real render path needs a video-renderer sidecar) but the stub drives
the real ``_materialize_video_post`` + real ``get_db_context`` + real DB
commit. The full-suite session-scoped workspace contamination across
e2e_smoke files is a pre-existing harness limitation (documented in Phase
4's module) and is out of scope — each scenario here passes in isolation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.services import release_proposal as rp
from roboco.services.release_executor import _GitReleaseOps, _ReleaseContext
from roboco.services.task import VIDEO_POST_SOURCE, VIDEO_SOURCE
from roboco.services.tiktok_client import LiveTikTokPoster
from roboco.services.tiktok_credentials import get_tiktok_credentials_service
from sqlalchemy import select

if TYPE_CHECKING:
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from tests.e2e_smoke.harness import E2EStack

_TWO_POLLS = 2


# ---------------------------------------------------------------------------
# H24 — wait_for_ci polls through the window on a non-success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h24_wait_for_ci_polls_through_non_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H24: a non-success on the same sha (a failed first attempt while a
    re-run is still in_progress) does not short-circuit ``return False``;
    the poll keeps going through the window and a subsequent ``success``
    still publishes. Drives the real ``_GitReleaseOps.wait_for_ci`` with a
    stubbed CI-conclusion source; ``_CI_POLL_INTERVAL_SECONDS`` is patched
    to 0 so the poll is instant."""
    from roboco.services import release_executor

    monkeypatch.setattr(release_executor, "_CI_POLL_INTERVAL_SECONDS", 0.0)

    ctx = _ReleaseContext(
        slug="roboco",
        default_branch="master",
        root=Path("/tmp/release-e2e"),
        git_url="",
        git_prefix=[],
        ci_workflow=None,
    )
    ops = _GitReleaseOps(session=MagicMock(), ctx=ctx)
    sha = "abc123"

    ci_results = [
        {"head_sha": sha, "conclusion": "failure"},
        {"head_sha": sha, "conclusion": "success"},
    ]
    fake_git = MagicMock()
    fake_git.get_latest_ci_conclusion = AsyncMock(side_effect=ci_results)

    with patch("roboco.services.git.get_git_service", return_value=fake_git):
        published = await ops.wait_for_ci(sha)

    assert published is True
    # It kept polling past the non-success — both polls were consumed.
    assert fake_git.get_latest_ci_conclusion.await_count == _TWO_POLLS


# ---------------------------------------------------------------------------
# H25 — sweep_orphan_release_locks against the real e2e Redis
# ---------------------------------------------------------------------------


async def _redis_alive(host: str, port: int) -> bool:
    import redis.asyncio as aioredis

    try:
        conn = aioredis.from_url(f"redis://{host}:{port}/15")
        try:
            await conn.ping()
        finally:
            await conn.aclose()
    except Exception:
        return False
    return True


@pytest.mark.asyncio
async def test_h25_sweep_deletes_orphan_release_locks(
    e2e_stack: E2EStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H25: a release-proposal mutex whose owner isn't in the in-flight
    registry is deleted; an in-flight one is preserved. Drives the real
    ``sweep_orphan_release_locks`` against the real e2e Redis (db 15,
    test-isolated). Skips when no local Redis is reachable."""
    from roboco.config import settings

    host, port = "127.0.0.1", 6379
    if not await _redis_alive(host, port):
        pytest.skip("no local Redis reachable on 127.0.0.1:6379; run `make infra`")

    # Override the autouse _no_live_redis patch for this test only.
    monkeypatch.setattr(settings, "redis_host", host)
    monkeypatch.setattr(settings, "redis_port", port)
    monkeypatch.setattr(settings, "redis_db", 15)

    import redis.asyncio as aioredis

    conn = aioredis.from_url(f"redis://{host}:{port}/15")
    try:
        await conn.flushdb()
        orphan_id = uuid4()
        in_flight_id = uuid4()
        orphan_key = f"{rp._RELEASE_LOCK_PREFIX}{orphan_id}"
        in_flight_key = f"{rp._RELEASE_LOCK_PREFIX}{in_flight_id}"
        await conn.set(orphan_key, "deadtoken")
        await conn.set(in_flight_key, "livetoken")

        rp._INFLIGHT_APPROVES.clear()
        rp._INFLIGHT_APPROVES[in_flight_id] = cast("asyncio.Task[None]", object())

        await rp.sweep_orphan_release_locks()

        assert await conn.get(orphan_key) is None  # orphan deleted
        assert await conn.get(in_flight_key) is not None  # in-flight preserved
    finally:
        rp._INFLIGHT_APPROVES.clear()
        await conn.flushdb()
        await conn.aclose()


# ---------------------------------------------------------------------------
# M1 — _refresh commits rotated tokens in an independent session
# ---------------------------------------------------------------------------


async def _seed_tiktok_row(factory: async_sessionmaker[AsyncSession]) -> None:
    """Seed the singleton ``tiktok_credentials`` row with encrypted tokens."""
    async with factory() as session:
        await get_tiktok_credentials_service(session).set_credentials(
            client_key="ck",
            client_secret="cs",
            access_token="acc-original",
            refresh_token="ref-original",
        )
        await session.commit()


@pytest.mark.asyncio
async def test_m1_refresh_rotated_tokens_survive_caller_rollback(
    e2e_stack: E2EStack,
) -> None:
    """M1: ``_refresh`` commits the rotated tokens in an independent session
    before returning; a lock-loss rollback of the caller's session does not
    discard them (TikTok already invalidated the old refresh_token → a
    discard was a permanent credential lockout). Seeds a row, drives the
    real ``_refresh`` with a mocked TikTok token endpoint, rolls the caller
    session back, and re-reads from a fresh session.

    The lazy engine (``_DbHolder``) is reset first so ``_refresh``'s
    internal ``get_session_factory()`` creates the engine in THIS test's
    loop — without the reset, prior API requests in the uvicorn thread bind
    the lazy engine to a different loop and asyncpg raises
    "Future attached to a different loop"."""
    from roboco.db import base as db_base

    db_base._DbHolder.engine = None
    db_base._DbHolder.session_factory = None
    factory = db_base.get_session_factory()
    try:
        await _seed_tiktok_row(factory)
        async with factory() as caller_sess:
            creds = await get_tiktok_credentials_service(caller_sess).get_decrypted()
            assert creds is not None
            poster = LiveTikTokPoster(
                creds, session=caller_sess, timeout=10.0, client=None
            )

            fake_resp = MagicMock()
            fake_resp.is_success = True
            fake_resp.json.return_value = {
                "access_token": "acc-rotated",
                "refresh_token": "ref-rotated",
            }
            fake_client = MagicMock()
            fake_client.post = AsyncMock(return_value=fake_resp)

            await poster._refresh(fake_client)

            # Caller rolls back (lock-loss path, video_post_service.py:274).
            await caller_sess.rollback()

        # Fresh session — rotated tokens must be durable despite the rollback.
        async with factory() as fresh:
            rotated = await get_tiktok_credentials_service(fresh).get_decrypted()
            assert rotated is not None
            assert rotated.access_token == "acc-rotated"
            assert rotated.refresh_token == "ref-rotated"
    finally:
        await db_base.close_db()


# ---------------------------------------------------------------------------
# M21 — _run_video_render_cycle commits per-task
# ---------------------------------------------------------------------------


async def _seed_video_render_stack(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[Any, Any]:
    """Seed system + secretary-1 (the video_post draft FK target), a project,
    and two COMPLETED ``source=video`` authoring tasks carrying a proposed
    composition. Returns (task_a_id, task_b_id)."""
    draft_a: dict[str, Any] = {
        "occasion": "m21-a",
        "script": "A shipped",
        "composition_id": "IntroA",
        "input_props": {"title": "a"},
        "x_caption": "A shipped",
        "tiktok_caption": "A shipped",
        "platforms": ["x", "tiktok"],
    }
    draft_b: dict[str, Any] = {
        "occasion": "m21-b",
        "script": "B shipped",
        "composition_id": "IntroB",
        "input_props": {"title": "b"},
        "x_caption": "B shipped",
        "tiktok_caption": "B shipped",
        "platforms": ["x", "tiktok"],
    }

    async with factory() as session:
        for agent_uuid, slug, role, team in (
            (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM, None),
            (
                _foundation.AGENTS["secretary-1"].uuid,
                "secretary-1",
                AgentRole.SECRETARY,
                None,
            ),
            (
                _foundation.AGENTS["ux-dev-1"].uuid,
                "ux-dev-1",
                AgentRole.DEVELOPER,
                Team.UX_UI,
            ),
        ):
            if await session.get(AgentTable, agent_uuid) is None:
                session.add(
                    AgentTable(
                        id=agent_uuid,
                        name=slug,
                        slug=slug,
                        role=role,
                        team=team,
                        status=AgentStatus.ACTIVE,
                        model_config={},
                        system_prompt=slug,
                        capabilities=[],
                        permissions={},
                        metrics={},
                    )
                )
        await session.flush()
        project = ProjectTable(
            id=uuid4(),
            name="m21-proj",
            slug=f"m21-proj-{uuid4().hex[:6]}",
            git_url="https://github.com/x/y.git",
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.UX_UI,
            created_by=_foundation.AGENTS["system"].uuid,
            is_active=True,
            video_engine_enabled=True,
        )
        session.add(project)
        await session.flush()

        def _task(draft: dict[str, Any], title: str) -> TaskTable:
            return TaskTable(
                id=uuid4(),
                title=title,
                description="d",
                acceptance_criteria=["rendered"],
                status=TaskStatus.COMPLETED,
                priority=2,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.LOW,
                team=Team.UX_UI,
                project_id=project.id,
                created_by=_foundation.AGENTS["system"].uuid,
                assigned_to=_foundation.AGENTS["ux-dev-1"].uuid,
                confirmed_by_human=True,
                source=VIDEO_SOURCE,
                orchestration_markers={markers.VIDEO_DRAFT: draft},
            )

        a = _task(draft_a, "Video: m21-a")
        b = _task(draft_b, "Video: m21-b")
        session.add_all([a, b])
        await session.flush()
        await session.commit()
        return a.id, b.id


@pytest.mark.asyncio
async def test_m21_render_cycle_commits_per_task(
    e2e_stack: E2EStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M21: a raise mid-cycle no longer rolls back prior renders. The cycle
    commits after each ``_render_video_task``; A's ``render_status='rendered'``
    + A's held video_post draft are durable before B raises. Drives the real
    ``_run_video_render_cycle`` (real ``get_db_context`` + real DB) with a
    stubbed ``_render_video_task`` — A's stub drives the real
    ``_materialize_video_post`` (real video_engine code path), B's raises."""
    from roboco.config import settings
    from roboco.db import base as db_base
    from roboco.runtime.orchestrator import AgentOrchestrator

    monkeypatch.setattr(settings, "video_engine_enabled", True)

    # Reset the lazy engine so ``get_db_context`` (used by the cycle) binds
    # to THIS test's loop, not the uvicorn thread's.
    db_base._DbHolder.engine = None
    db_base._DbHolder.session_factory = None
    factory = db_base.get_session_factory()
    try:
        await _seed_video_render_stack(factory)

        orch: Any = AgentOrchestrator.__new__(AgentOrchestrator)
        # ``list_completed_video_tasks`` orders by created_at.desc(), so the
        # later-seeded task is processed first. The stub is order-independent:
        # the first task renders (real _materialize_video_post), the second
        # raises — proving the first's commit is durable before the raise.
        rendered_id: list[Any] = []

        async def _stub_render(db: Any, task: Any) -> None:
            if rendered_id:
                raise RuntimeError("B blew up")
            rendered_id.append(task.id)
            draft = markers.get_video_draft(task) or {}
            await orch._materialize_video_post(
                db,
                task,
                draft,
                {"vertical": "/fake/v.mp4", "square": "/fake/s.mp4"},
            )

        orch._render_video_task = AsyncMock(side_effect=_stub_render)

        with pytest.raises(RuntimeError, match="B blew up"):
            await orch._run_video_render_cycle()

        # Re-read from a fresh session — the rendered task's marker is
        # durable (committed before the second raised), and its held
        # video_post draft exists.
        first_id = rendered_id[0]
        async with factory() as fresh:
            a_row = (
                await fresh.execute(select(TaskTable).where(TaskTable.id == first_id))
            ).scalar_one()
            a_draft = markers.get_video_draft(a_row) or {}
            assert a_draft.get("render_status") == "rendered"

            post_row = (
                await fresh.execute(
                    select(TaskTable).where(
                        TaskTable.source == VIDEO_POST_SOURCE,
                        TaskTable.status != TaskStatus.CANCELLED,
                    )
                )
            ).scalar_one_or_none()
            assert post_row is not None
            assert post_row.confirmed_by_human is False  # held for CEO
            post_draft = markers.get_video_draft(post_row) or {}
            assert post_draft.get("source_task_id") == str(first_id)
            assert post_draft.get("render_status") == "rendered"
    finally:
        await db_base.close_db()
