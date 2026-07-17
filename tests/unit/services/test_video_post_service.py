"""VideoPostService coverage: approve posts per-platform (idempotent), reject
cancels.

Mirrors the X-post service tests. The heartbeat-mutex acquire/release are
patched (no live Redis in tests, matching the project's `_no_live_redis`
fixture) so approve exercises the real per-platform dispatch + status-
transition path; the run_guarded renew loop is left unpatched — its heartbeat
calls fail fast against the poisoned test Redis and are swallowed exactly as
in production, so no separate mock is needed there.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    Team,
)
from roboco.models.base import TaskNature as TN
from roboco.models.base import TaskStatus as TS
from roboco.models.base import TaskType as TT
from roboco.services.heartbeat_mutex import HeartbeatMutex
from roboco.services.task import VIDEO_POST_SOURCE, TaskService
from roboco.services.video_post_service import (
    TaskAlreadyCompletedError,
    TikTokPoster,
    TikTokUploadResult,
    VideoCaptionTooLongError,
    VideoPostService,
    XVideoPoster,
    XVideoPostResult,
    get_video_post_service,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from uuid import UUID

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SECRETARY_UUID = _foundation.AGENTS["secretary-1"].uuid
TWO = 2
VERTICAL_MP4 = "/render/out/1-vertical.mp4"
SQUARE_MP4 = "/render/out/1-square.mp4"


class _StubXPoster(XVideoPoster):
    def __init__(
        self,
        *,
        posted: bool = True,
        video_id: str = "x-vid-1",
        raises: bool = False,
        configured: bool = True,
    ) -> None:
        self._posted = posted
        self._video_id = video_id
        self._raises = raises
        self._configured = configured
        self.calls: list[tuple[str, str]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def post_video(self, *, mp4_path: str, caption: str) -> XVideoPostResult:
        self.calls.append((mp4_path, caption))
        if self._raises:
            raise RuntimeError("simulated X network failure")
        if not self._posted:
            return XVideoPostResult(posted=False, video_id=None, detail="rejected by X")
        return XVideoPostResult(posted=True, video_id=self._video_id, detail="posted")


class _StubTikTokPoster(TikTokPoster):
    def __init__(
        self,
        *,
        uploaded: bool = True,
        publish_id: str = "tt-pub-1",
        raises: bool = False,
        configured: bool = True,
    ) -> None:
        self._uploaded = uploaded
        self._publish_id = publish_id
        self._raises = raises
        self._configured = configured
        self.calls: list[tuple[str, str]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def upload_to_inbox(
        self, *, mp4_path: str, caption: str
    ) -> TikTokUploadResult:
        self.calls.append((mp4_path, caption))
        if self._raises:
            raise RuntimeError("simulated TikTok network failure")
        if not self._uploaded:
            return TikTokUploadResult(
                uploaded=False, publish_id=None, detail="rejected by TikTok"
            )
        return TikTokUploadResult(
            uploaded=True, publish_id=self._publish_id, detail="uploaded"
        )


async def _seed_video_post(
    session: AsyncSession,
    *,
    platforms: list[str] | None = None,
    x_caption: str = "Check out this clip",
    tiktok_caption: str = "Check out this clip on TikTok",
) -> TaskTable:
    for uuid, slug, role in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM),
        (SECRETARY_UUID, "secretary-1", AgentRole.SECRETARY),
    ):
        if await session.get(AgentTable, uuid) is None:
            session.add(
                AgentTable(
                    id=uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=None,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo",
        slug=f"roboco-{uuid4().hex[:6]}",
        git_url="https://example.com/roboco.git",
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title="Video post: release 1.0",
        description="script",
        acceptance_criteria=["CEO approves or rejects the draft"],
        status=TS.PENDING,
        priority=2,
        task_type=TT.ADMINISTRATIVE,
        nature=TN.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        project_id=project.id,
        created_by=SYSTEM_UUID,
        assigned_to=SECRETARY_UUID,
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
            "platforms": platforms if platforms is not None else ["x", "tiktok"],
            "mp4_paths": {"vertical": VERTICAL_MP4, "square": SQUARE_MP4},
            "x_caption": x_caption,
            "tiktok_caption": tiktok_caption,
            "render_status": "rendered",
        },
    )
    await session.flush()
    return task


def _svc(
    session: AsyncSession, *, x_poster: XVideoPoster, tiktok_poster: TikTokPoster
) -> VideoPostService:
    return get_video_post_service(
        session, x_poster=x_poster, tiktok_poster=tiktok_poster
    )


def _id(task: TaskTable) -> UUID:
    """The ORM id typed as stdlib ``uuid.UUID`` for service-call sites."""
    return cast("UUID", task.id)


_LOCKED = (
    patch.object(HeartbeatMutex, "acquire", AsyncMock(return_value="tok")),
    patch.object(HeartbeatMutex, "release", AsyncMock(return_value=None)),
)


@pytest.mark.asyncio
async def test_approve_posts_both_platforms_and_completes(
    db_session: AsyncSession,
) -> None:
    task = await _seed_video_post(db_session)
    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster()
    with _LOCKED[0], _LOCKED[1]:
        result = await _svc(
            db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
        ).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    assert result.posted == {"x": "x-vid-1", "tiktok": "tt-pub-1"}
    # X gets the square cut, TikTok the vertical cut.
    assert x_poster.calls == [(SQUARE_MP4, "Check out this clip")]
    assert tiktok_poster.calls == [(VERTICAL_MP4, "Check out this clip on TikTok")]
    await db_session.refresh(task)
    assert task.status == TS.COMPLETED
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["x_posted_id"] == "x-vid-1"
    assert draft["tiktok_posted_id"] == "tt-pub-1"


@pytest.mark.asyncio
async def test_approve_single_platform_only_calls_that_poster(
    db_session: AsyncSession,
) -> None:
    task = await _seed_video_post(db_session, platforms=["x"])
    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster()
    with _LOCKED[0], _LOCKED[1]:
        result = await _svc(
            db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
        ).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    assert result.posted == {"x": "x-vid-1"}
    assert x_poster.calls == [(SQUARE_MP4, "Check out this clip")]
    assert tiktok_poster.calls == []  # never invoked — not in this draft's platforms


@pytest.mark.asyncio
async def test_approve_completes_when_unconfigured_platform_is_skipped(
    db_session: AsyncSession,
) -> None:
    """The live lingering-card defect: X posts, TikTok has no credentials —
    the draft must COMPLETE (skipped, not pending-forever)."""
    task = await _seed_video_post(db_session)
    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster(configured=False)
    with _LOCKED[0], _LOCKED[1]:
        result = await _svc(
            db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
        ).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    assert result.posted == {"x": "x-vid-1"}
    assert "skipped (unconfigured): tiktok" in result.detail
    assert tiktok_poster.calls == []  # never attempted without credentials
    await db_session.refresh(task)
    assert task.status == TS.COMPLETED
    draft = markers.get_video_draft(task)
    assert draft is not None
    assert draft["x_posted_id"] == "x-vid-1"
    assert "tiktok_posted_id" not in draft


@pytest.mark.asyncio
async def test_approve_refuses_when_no_platform_is_configured(
    db_session: AsyncSession,
) -> None:
    """All targets unconfigured: never silently complete a draft that
    reached no audience."""
    task = await _seed_video_post(db_session)
    with _LOCKED[0], _LOCKED[1]:
        result = await _svc(
            db_session,
            x_poster=_StubXPoster(configured=False),
            tiktok_poster=_StubTikTokPoster(configured=False),
        ).approve(_id(task))
    assert result is not None
    assert result.status == "post_failed"
    assert "no target platform has credentials configured" in result.detail
    await db_session.refresh(task)
    assert task.status != TS.COMPLETED


@pytest.mark.asyncio
async def test_reapprove_after_partial_post_skips_x_and_completes(
    db_session: AsyncSession,
) -> None:
    """The exact recovery path for a card parked by an unconfigured
    platform: X already posted on a prior approve, TikTok unconfigured —
    re-approve must not re-post X and must clear the card."""
    task = await _seed_video_post(db_session)
    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster(configured=False)
    svc = _svc(db_session, x_poster=x_poster, tiktok_poster=tiktok_poster)
    draft = markers.get_video_draft(task)
    assert draft is not None
    markers.set_video_draft(task, {**draft, "x_posted_id": "x-vid-prior"})
    await db_session.flush()
    with _LOCKED[0], _LOCKED[1]:
        result = await svc.approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    assert result.posted == {"x": "x-vid-prior"}
    assert x_poster.calls == []  # already-posted guard held — no double post
    await db_session.refresh(task)
    assert task.status == TS.COMPLETED


@pytest.mark.asyncio
async def test_approve_is_idempotent_second_call_is_noop(
    db_session: AsyncSession,
) -> None:
    task = await _seed_video_post(db_session)
    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster()
    svc = _svc(db_session, x_poster=x_poster, tiktok_poster=tiktok_poster)
    with _LOCKED[0], _LOCKED[1]:
        first = await svc.approve(_id(task))
        second = await svc.approve(_id(task))
    assert first is not None
    assert first.status == "posted"
    assert second is not None
    assert second.status == "already_posted"
    assert second.posted == {"x": "x-vid-1", "tiktok": "tt-pub-1"}
    # Neither poster was called a second time.
    assert len(x_poster.calls) == 1
    assert len(tiktok_poster.calls) == 1


@pytest.mark.asyncio
async def test_approve_concurrent_lock_held_returns_in_progress(
    db_session: AsyncSession,
) -> None:
    task = await _seed_video_post(db_session)
    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster()
    with patch.object(HeartbeatMutex, "acquire", AsyncMock(return_value=None)):
        result = await _svc(
            db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
        ).approve(_id(task))
    assert result is not None
    assert result.status == "already_in_progress"
    assert x_poster.calls == []
    assert tiktok_poster.calls == []


@pytest.mark.asyncio
async def test_approve_redis_unavailable_fails_closed(db_session: AsyncSession) -> None:
    task = await _seed_video_post(db_session)
    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster()
    broken = MagicMock()
    broken.set = AsyncMock(side_effect=ConnectionError("redis down"))
    broken.aclose = AsyncMock()
    with patch("roboco.services.heartbeat_mutex.redis.from_url", return_value=broken):
        result = await _svc(
            db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
        ).approve(_id(task))
    assert result is not None
    assert result.status == "redis_unavailable"
    assert x_poster.calls == []
    assert tiktok_poster.calls == []
    await db_session.refresh(task)
    assert task.status == TS.PENDING  # never advanced without the mutex


@pytest.mark.asyncio
async def test_approve_applies_edited_captions_before_posting(
    db_session: AsyncSession,
) -> None:
    task = await _seed_video_post(db_session)
    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster()
    with _LOCKED[0], _LOCKED[1]:
        result = await _svc(
            db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
        ).approve(
            _id(task),
            x_caption="Edited X caption",
            tiktok_caption="Edited TikTok caption",
        )
    assert result is not None
    assert result.status == "posted"
    assert x_poster.calls == [(SQUARE_MP4, "Edited X caption")]
    assert tiktok_poster.calls == [(VERTICAL_MP4, "Edited TikTok caption")]


@pytest.mark.asyncio
async def test_approve_rejects_edited_x_caption_over_280_chars(
    db_session: AsyncSession,
) -> None:
    task = await _seed_video_post(db_session)
    svc = _svc(db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster())
    with pytest.raises(VideoCaptionTooLongError):
        await svc.approve(_id(task), x_caption="x" * 281)


@pytest.mark.asyncio
async def test_approve_rejects_edited_tiktok_caption_over_2200_chars(
    db_session: AsyncSession,
) -> None:
    task = await _seed_video_post(db_session)
    svc = _svc(db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster())
    with pytest.raises(VideoCaptionTooLongError):
        await svc.approve(_id(task), tiktok_caption="x" * 2201)


@pytest.mark.asyncio
async def test_approve_partial_failure_keeps_task_open_and_persists_the_success(
    db_session: AsyncSession, _test_database_url: str
) -> None:
    """Unlike every other approve() test in this file, this one commits a
    non-terminal (PENDING) video_post row via db_session and never drives it
    to a terminal state — every other test either never commits (rolled
    back at teardown) or ends COMPLETED (excluded from the open-task
    queries). Left behind, it leaks into the shared session-scoped test DB
    and breaks list_open_video_posts()/list_open_video_post_drafts()
    assertions in test_video_engine.py / test_video_render_loop.py, which
    run later in the same pytest session. Clean it up via its own
    committed session regardless of pass/fail."""
    task = await _seed_video_post(db_session)
    task_id = _id(task)
    project_id = task.project_id
    try:
        x_poster = _StubXPoster(posted=False)
        tiktok_poster = _StubTikTokPoster()
        with _LOCKED[0], _LOCKED[1]:
            result = await _svc(
                db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
            ).approve(task_id)
        assert result is not None
        assert result.status == "posted_partial"
        assert result.posted == {"tiktok": "tt-pub-1"}
        await db_session.refresh(task)
        assert task.status == TS.PENDING  # not completed — X still needs a retry
        draft = markers.get_video_draft(task)
        assert draft is not None
        assert draft["tiktok_posted_id"] == "tt-pub-1"
        assert "x_posted_id" not in draft
    finally:
        cleanup, cleanup_engine = await _fresh_session(_test_database_url)
        try:
            await cleanup.execute(delete(TaskTable).where(TaskTable.id == task_id))
            await cleanup.execute(
                delete(ProjectTable).where(ProjectTable.id == project_id)
            )
            await cleanup.commit()
        finally:
            await _dispose(cleanup, cleanup_engine)


@pytest.mark.asyncio
async def test_approve_retry_skips_the_already_posted_platform(
    db_session: AsyncSession,
) -> None:
    """A retry after a partial failure must not re-post the platform that
    already succeeded — only the still-pending one is attempted again."""
    task = await _seed_video_post(db_session)
    x_poster = _StubXPoster(posted=False)
    tiktok_poster = _StubTikTokPoster()
    svc = _svc(db_session, x_poster=x_poster, tiktok_poster=tiktok_poster)
    with _LOCKED[0], _LOCKED[1]:
        first = await svc.approve(_id(task))
        assert first is not None
        assert first.status == "posted_partial"
        x_poster._posted = True  # credentials fixed between attempts
        second = await svc.approve(_id(task))
    assert second is not None
    assert second.status == "posted"
    assert second.posted == {"x": "x-vid-1", "tiktok": "tt-pub-1"}
    # TikTok succeeded on attempt 1 and must not be re-uploaded on the retry;
    # X failed on attempt 1 so IS legitimately retried (and succeeds this time).
    assert len(tiktok_poster.calls) == 1
    assert len(x_poster.calls) == TWO


async def _fresh_session(url: str) -> tuple[AsyncSession, AsyncEngine]:
    """A session on a brand-new engine/connection (caller disposes)."""
    engine = create_async_engine(url, future=True)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    return factory(), engine


async def _dispose(session: AsyncSession, engine: AsyncEngine) -> None:
    with contextlib.suppress(Exception):
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_approve_partial_failure_persists_success_durably_across_sessions(
    db_session: AsyncSession, _test_database_url: str
) -> None:
    """`db_session` read-your-own-writes can hide a durability gap: platform
    1 (x) posts, platform 2 (tiktok)'s poster RAISES. X's posted-id must be
    committed for real — visible from a completely INDEPENDENT session and
    connection, not merely flushed on the session that ran approve() — and a
    retry from yet another fresh session must not re-invoke x's poster."""
    task = await _seed_video_post(db_session)
    task_id = _id(task)
    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster(raises=True)
    with _LOCKED[0], _LOCKED[1]:
        result = await _svc(
            db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
        ).approve(task_id)
    assert result is not None
    assert result.status == "posted_partial"
    assert result.posted == {"x": "x-vid-1"}

    # A brand-new session/connection — never touched db_session — must see
    # x's posted id as durably committed, not just flushed-in-memory there.
    fresh, fresh_engine = await _fresh_session(_test_database_url)
    try:
        fresh_task = await fresh.get(TaskTable, task_id)
        assert fresh_task is not None
        assert fresh_task.status == TS.PENDING
        fresh_draft = markers.get_video_draft(fresh_task)
        assert fresh_draft is not None
        assert fresh_draft["x_posted_id"] == "x-vid-1"
        assert "tiktok_posted_id" not in fresh_draft
    finally:
        await _dispose(fresh, fresh_engine)

    # Retry from yet ANOTHER fresh session (a brand-new request): x must not
    # be re-invoked — only the still-pending tiktok platform is retried.
    tiktok_poster._raises = False
    retry_session, retry_engine = await _fresh_session(_test_database_url)
    try:
        with _LOCKED[0], _LOCKED[1]:
            retry = await _svc(
                retry_session, x_poster=x_poster, tiktok_poster=tiktok_poster
            ).approve(task_id)
    finally:
        await _dispose(retry_session, retry_engine)
    assert retry is not None
    assert retry.status == "posted"
    assert retry.posted == {"x": "x-vid-1", "tiktok": "tt-pub-1"}
    assert len(x_poster.calls) == 1  # never re-invoked on the retry
    assert len(tiktok_poster.calls) == TWO  # the raise, then the successful retry


@pytest.mark.asyncio
async def test_approve_unknown_task_returns_none(db_session: AsyncSession) -> None:
    result = await _svc(
        db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster()
    ).approve(uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_reject_records_reason_and_cancels(db_session: AsyncSession) -> None:
    task = await _seed_video_post(db_session)
    with _LOCKED[0], _LOCKED[1]:
        updated = await _svc(
            db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster()
        ).reject(_id(task), "Doesn't match the release")
    assert updated is not None
    assert updated.status == TS.CANCELLED
    assert markers.get_video_reject_reason(updated) == "Doesn't match the release"


@pytest.mark.asyncio
async def test_reject_with_reason_calls_reauthor_with_cancelled_task(
    db_session: AsyncSession,
) -> None:
    """A non-blank reject reason routes into VideoEngine.reauthor_from_rejection,
    called with the just-cancelled task and the verbatim reason."""
    task = await _seed_video_post(db_session)
    fake_engine = MagicMock()
    fake_engine.reauthor_from_rejection = AsyncMock(return_value=None)
    with (
        _LOCKED[0],
        _LOCKED[1],
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=fake_engine,
        ),
    ):
        updated = await _svc(
            db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster()
        ).reject(_id(task), "Doesn't match the release")
    assert updated is not None
    assert updated.status == TS.CANCELLED
    fake_engine.reauthor_from_rejection.assert_awaited_once()
    called_task, called_reason = fake_engine.reauthor_from_rejection.await_args.args
    assert called_task.id == task.id
    assert called_reason == "Doesn't match the release"


@pytest.mark.asyncio
async def test_reject_succeeds_even_when_reauthor_raises(
    db_session: AsyncSession,
) -> None:
    """A reauthor failure must never fail or roll back the reject — the
    cancel already committed before this best-effort seam runs."""
    task = await _seed_video_post(db_session)
    fake_engine = MagicMock()
    fake_engine.reauthor_from_rejection = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        _LOCKED[0],
        _LOCKED[1],
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=fake_engine,
        ),
    ):
        updated = await _svc(
            db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster()
        ).reject(_id(task), "Doesn't match the release")
    assert updated is not None
    assert updated.status == TS.CANCELLED
    assert markers.get_video_reject_reason(updated) == "Doesn't match the release"


@pytest.mark.asyncio
async def test_reject_blank_reason_skips_reauthor(db_session: AsyncSession) -> None:
    task = await _seed_video_post(db_session)
    fake_engine = MagicMock()
    fake_engine.reauthor_from_rejection = AsyncMock()
    with (
        _LOCKED[0],
        _LOCKED[1],
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=fake_engine,
        ),
    ):
        updated = await _svc(
            db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster()
        ).reject(_id(task), "   ")
    assert updated is not None
    assert updated.status == TS.CANCELLED
    fake_engine.reauthor_from_rejection.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_takes_the_same_lock_approve_holds(
    db_session: AsyncSession,
) -> None:
    """A reject under the real acquire/release path still lands (mirrors the
    approve happy-path locking) — proves the mutex round-trip, not just the
    mutation."""
    task = await _seed_video_post(db_session)
    with _LOCKED[0], _LOCKED[1]:
        updated = await _svc(
            db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster()
        ).reject(_id(task), "Doesn't match the release")
    assert updated is not None
    assert updated.status == TS.CANCELLED
    _LOCKED[0].new.assert_awaited()
    _LOCKED[1].new.assert_awaited()


@pytest.mark.asyncio
async def test_reject_concurrent_lock_held_refuses(db_session: AsyncSession) -> None:
    """A reject arriving while a concurrent approve holds the lock must not
    cancel a draft that may be mid-post — same refusal as approve's own
    already-in-progress case."""
    task = await _seed_video_post(db_session)
    with patch.object(HeartbeatMutex, "acquire", AsyncMock(return_value=None)):
        result = await _svc(
            db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster()
        ).reject(_id(task), "Doesn't match the release")
    assert result is None
    await db_session.refresh(task)
    assert task.status == TS.PENDING  # never cancelled while the lock was held


@pytest.mark.asyncio
async def test_reject_redis_unavailable_refuses(db_session: AsyncSession) -> None:
    """Reject fails CLOSED when Redis is unreachable, mirroring approve: an
    approve that took the lock while Redis was up stays authoritative through
    the heartbeat grace window after Redis drops, so an unlocked reject could
    CANCEL a draft that approve is mid-posting. The CEO retries once Redis is
    back."""
    task = await _seed_video_post(db_session)
    broken = MagicMock()
    broken.set = AsyncMock(side_effect=ConnectionError("redis down"))
    broken.aclose = AsyncMock()
    with patch("roboco.services.heartbeat_mutex.redis.from_url", return_value=broken):
        result = await _svc(
            db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster()
        ).reject(_id(task), "Doesn't match the release")
    assert result is None
    await db_session.refresh(task)
    assert task.status == TS.PENDING  # never cancelled without the mutex


@pytest.mark.asyncio
async def test_list_held_video_posts_excludes_terminal(
    db_session: AsyncSession,
) -> None:
    open_task = await _seed_video_post(db_session)
    rejected_task = await _seed_video_post(db_session)
    svc = _svc(db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster())
    with _LOCKED[0], _LOCKED[1]:
        await svc.reject(_id(rejected_task), "not relevant")
    held = await svc.list_held_video_posts()
    ids = {t.id for t in held}
    assert open_task.id in ids
    assert rejected_task.id not in ids


@pytest.mark.asyncio
async def test_list_video_post_history_excludes_open_drafts(
    db_session: AsyncSession, _test_database_url: str
) -> None:
    """approve() commits the whole session, so open_task's still-uncommitted
    seed insert becomes durable too (same class of leak documented on
    test_approve_partial_failure_keeps_task_open_and_persists_the_success) —
    clean it up explicitly so it doesn't pollute list_open_video_posts()/
    list_open_video_post_drafts() assertions elsewhere in the suite."""
    open_task = await _seed_video_post(db_session)
    open_task_id = _id(open_task)
    open_project_id = open_task.project_id
    posted_task = await _seed_video_post(db_session, platforms=["x"])
    svc = _svc(db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster())
    try:
        with _LOCKED[0], _LOCKED[1]:
            await svc.approve(_id(posted_task))
        history = await svc.list_video_post_history()
        ids = {t.id for t in history}
        assert posted_task.id in ids
        assert open_task.id not in ids
    finally:
        cleanup, cleanup_engine = await _fresh_session(_test_database_url)
        try:
            await cleanup.execute(delete(TaskTable).where(TaskTable.id == open_task_id))
            await cleanup.execute(
                delete(ProjectTable).where(ProjectTable.id == open_project_id)
            )
            await cleanup.commit()
        finally:
            await _dispose(cleanup, cleanup_engine)


@pytest.mark.asyncio
async def test_list_video_post_history_newest_acted_first(
    db_session: AsyncSession,
) -> None:
    rejected_task = await _seed_video_post(db_session)
    svc = _svc(db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster())
    with _LOCKED[0], _LOCKED[1]:
        await svc.reject(_id(rejected_task), "wrong occasion")
    posted_task = await _seed_video_post(db_session, platforms=["x"])
    with _LOCKED[0], _LOCKED[1]:
        await svc.approve(_id(posted_task))
    history = await svc.list_video_post_history()
    ids = [t.id for t in history]
    assert ids.index(posted_task.id) < ids.index(rejected_task.id)


@pytest.mark.asyncio
async def test_list_video_post_history_includes_marker_fields(
    db_session: AsyncSession,
) -> None:
    posted_task = await _seed_video_post(db_session, platforms=["x"])
    svc = _svc(
        db_session,
        x_poster=_StubXPoster(video_id="xid9"),
        tiktok_poster=_StubTikTokPoster(),
    )
    with _LOCKED[0], _LOCKED[1]:
        await svc.approve(_id(posted_task))
    rejected_task = await _seed_video_post(db_session)
    with _LOCKED[0], _LOCKED[1]:
        await svc.reject(_id(rejected_task), "off-brand")

    history = await svc.list_video_post_history()
    by_id = {t.id: t for t in history}
    posted_draft = markers.get_video_draft(by_id[posted_task.id])
    assert posted_draft is not None
    assert posted_draft["x_posted_id"] == "xid9"
    assert markers.get_video_reject_reason(by_id[rejected_task.id]) == "off-brand"


@pytest.mark.asyncio
async def test_list_video_post_history_respects_limit(
    db_session: AsyncSession,
) -> None:
    svc = _svc(db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster())
    tasks = []
    for _ in range(3):
        t = await _seed_video_post(db_session)
        with _LOCKED[0], _LOCKED[1]:
            await svc.reject(_id(t), "not relevant")
        tasks.append(t)
    history = await svc.list_video_post_history(limit=2)
    assert len(history) == TWO
    ids = {t.id for t in history}
    assert tasks[2].id in ids
    assert tasks[1].id in ids
    assert tasks[0].id not in ids


@pytest.mark.asyncio
async def test_approve_commits_before_releasing_the_lock(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The double-post guard depends on this ordering: every commit (each
    platform success, then COMPLETED) must be durable before the lock is
    released, or a racing approve could acquire the lock the instant it's
    dropped and post again before a commit lands. Both platforms succeed
    here, so this lands 3 commits (one per platform + COMPLETED) — asserts
    the ordering property, not a hard-coded count of an implementation
    detail."""
    task = await _seed_video_post(db_session)
    order: list[str] = []

    real_commit = db_session.commit

    async def _spy_commit() -> None:
        await real_commit()
        order.append("commit")

    monkeypatch.setattr(db_session, "commit", _spy_commit)

    async def _spy_release(_self: HeartbeatMutex, _token: str) -> None:
        order.append("release")

    with (
        patch.object(HeartbeatMutex, "acquire", AsyncMock(return_value="tok")),
        patch.object(HeartbeatMutex, "release", _spy_release),
    ):
        result = await _svc(
            db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster()
        ).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    assert order[-1] == "release"
    assert order.count("release") == 1
    assert order[:-1] == ["commit"] * (len(order) - 1)
    assert len(order) > 1  # at least one commit landed before the release


@pytest.mark.asyncio
async def test_approve_rechecks_completed_under_lock_and_never_reposts(
    db_session: AsyncSession,
) -> None:
    """A concurrent approve wins the lock, posts, and commits COMPLETED after
    our pre-lock read. Once we acquire the lock, the in-lock re-read must see
    COMPLETED and short-circuit — never re-posting."""
    task = await _seed_video_post(db_session)
    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster()

    async def _win_the_race(_self: HeartbeatMutex) -> str:
        draft = dict(markers.get_video_draft(task) or {})
        draft["x_posted_id"] = "x-winner"
        draft["tiktok_posted_id"] = "tt-winner"
        markers.set_video_draft(task, draft)
        task.status = TS.COMPLETED
        await db_session.flush()
        return "tok"

    with (
        patch.object(HeartbeatMutex, "acquire", _win_the_race),
        patch.object(HeartbeatMutex, "release", AsyncMock(return_value=None)),
    ):
        result = await _svc(
            db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
        ).approve(_id(task))
    assert result is not None
    assert result.status == "already_posted"
    assert result.posted == {"x": "x-winner", "tiktok": "tt-winner"}
    assert x_poster.calls == []
    assert tiktok_poster.calls == []


@pytest.mark.asyncio
async def test_approve_concurrent_caption_edit_does_not_erase_a_committed_posted_id(
    db_session: AsyncSession, _test_database_url: str
) -> None:
    """RC1 regression: a caption edit must never write the draft column
    before the lock. The old pre-lock flush computed its update from a read
    taken before a genuinely concurrent approve (a different session/
    connection) posted + committed a platform's id; once THIS session later
    commits under the lock, that stale write won and erased the concurrent
    commit — the retry re-posted the platform (a double-post)."""
    task = await _seed_video_post(db_session, platforms=["x", "tiktok"])
    task_id = _id(task)
    await db_session.commit()  # externally visible to the "concurrent" session below

    real_get = TaskService.get
    injected = False

    async def _get_then_inject_concurrent_post(
        self: TaskService, tid: UUID
    ) -> TaskTable | None:
        """Fires once, right after the outer pre-lock read — the exact
        window between our read and our own (would-be) pre-lock write."""
        nonlocal injected
        result = await real_get(self, tid)
        if not injected:
            injected = True
            other, other_engine = await _fresh_session(_test_database_url)
            try:
                other_task = await other.get(TaskTable, tid)
                assert other_task is not None
                other_draft = dict(markers.get_video_draft(other_task) or {})
                other_draft["x_posted_id"] = "x-concurrent"
                markers.set_video_draft(other_task, other_draft)
                await other.commit()
            finally:
                await _dispose(other, other_engine)
        return result

    x_poster = _StubXPoster()
    tiktok_poster = _StubTikTokPoster()
    with (
        patch.object(TaskService, "get", _get_then_inject_concurrent_post),
        _LOCKED[0],
        _LOCKED[1],
    ):
        result = await _svc(
            db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
        ).approve(task_id, x_caption="Edited X caption")

    assert result is not None
    # X must never be re-posted — the concurrently committed id survives.
    assert x_poster.calls == []
    assert result.posted.get("x") == "x-concurrent"
    assert tiktok_poster.calls == [(VERTICAL_MP4, "Check out this clip on TikTok")]

    fresh, fresh_engine = await _fresh_session(_test_database_url)
    try:
        final = await fresh.get(TaskTable, task_id)
        assert final is not None
        final_draft = markers.get_video_draft(final)
        assert final_draft is not None
        assert final_draft["x_posted_id"] == "x-concurrent"  # not erased
        assert final_draft["x_caption"] == "Edited X caption"  # edit still applied
    finally:
        await _dispose(fresh, fresh_engine)


@pytest.mark.asyncio
async def test_reject_completed_raises(db_session: AsyncSession) -> None:
    """An already-posted (COMPLETED) video draft is live on X/TikTok;
    rejecting it would lie "cancelled (never posted)" while the clip is public."""
    task = await _seed_video_post(db_session)
    draft = dict(markers.get_video_draft(task) or {})
    draft["x_posted_id"] = "x-vid"
    draft["tiktok_posted_id"] = "tt-pub"
    markers.set_video_draft(task, draft)
    task.status = TS.COMPLETED
    await db_session.flush()
    with pytest.raises(TaskAlreadyCompletedError):
        await _svc(
            db_session, x_poster=_StubXPoster(), tiktok_poster=_StubTikTokPoster()
        ).reject(_id(task), "nope")


@pytest.mark.asyncio
async def test_approve_cancel_during_a_platform_commit_leaves_it_durable_and_usable(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    _test_database_url: str,
) -> None:
    """RC2 regression: a lock-loss cancellation firing while a per-platform
    commit is in flight used to interrupt asyncio's own await on that
    commit — rolling it back (so a retry re-posts the same platform) and
    leaving the session unusable for anything that runs after (a route's
    get_db teardown 500s on the poisoned session). Fixed: the commit is
    asyncio.shield()ed so it always finishes, and the lock_lost path rolls
    back explicitly so the session is guaranteed clean for reuse.

    A lock_lost result never reaches _finalize_post, so the task is left
    PENDING (non-terminal) even though its one platform posted — unlike
    every other approve() test in this file, this one must clean up its own
    committed row (see the note on test_approve_partial_failure_keeps_task_
    open_and_persists_the_success for why that matters)."""
    task = await _seed_video_post(db_session, platforms=["x"])
    task_id = _id(task)
    project_id = task.project_id
    await db_session.commit()  # a prior committed transaction, isolating the
    # racy platform-commit below to its OWN transaction — so a rollback of
    # that transaction can't also undo the seed itself.

    try:
        x_poster = _StubXPoster()
        tiktok_poster = _StubTikTokPoster()

        real_commit = db_session.commit
        commit_started = asyncio.Event()

        async def _slow_commit() -> None:
            commit_started.set()
            await real_commit()

        monkeypatch.setattr(db_session, "commit", _slow_commit)

        async def _lose_the_lock_once_committing(
            _self: HeartbeatMutex, _token: str
        ) -> bool:
            await commit_started.wait()
            return False  # lock lost -- cancels the guarded task fail-closed

        with (
            patch.object(HeartbeatMutex, "acquire", AsyncMock(return_value="tok")),
            patch.object(HeartbeatMutex, "release", AsyncMock(return_value=None)),
            patch.object(
                HeartbeatMutex, "heartbeat_once", _lose_the_lock_once_committing
            ),
        ):
            result = await _svc(
                db_session, x_poster=x_poster, tiktok_poster=tiktok_poster
            ).approve(task_id)

        assert result is not None
        assert result.status == "lock_lost"
        assert x_poster.calls == [(SQUARE_MP4, "Check out this clip")]  # it DID post

        # Durable: a brand-new connection sees the id even though the
        # guarded task was cancelled mid-commit.
        fresh, fresh_engine = await _fresh_session(_test_database_url)
        try:
            final = await fresh.get(TaskTable, task_id)
            assert final is not None
            final_draft = markers.get_video_draft(final)
            assert final_draft is not None
            assert final_draft["x_posted_id"] == "x-vid-1"
        finally:
            await _dispose(fresh, fresh_engine)

        # The session itself must still be usable afterward — not poisoned
        # by the cancelled-mid-commit path.
        monkeypatch.setattr(db_session, "commit", real_commit)
        check = await db_session.get(TaskTable, task_id)
        assert check is not None
        await db_session.commit()  # would raise if the session were poisoned
    finally:
        cleanup, cleanup_engine = await _fresh_session(_test_database_url)
        try:
            await cleanup.execute(delete(TaskTable).where(TaskTable.id == task_id))
            await cleanup.execute(
                delete(ProjectTable).where(ProjectTable.id == project_id)
            )
            await cleanup.commit()
        finally:
            await _dispose(cleanup, cleanup_engine)
