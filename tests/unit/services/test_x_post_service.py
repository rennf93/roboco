"""XPostService coverage: approve posts (idempotent), reject cancels.

Mirrors the release-proposal service tests. The Redis lock helpers are
patched (no live Redis in tests, matching the project's ``_no_live_redis``
fixture) so approve exercises the real post + status-transition path.
"""

from __future__ import annotations

import contextlib
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings as cfg
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
from roboco.services.task import (
    X_FEATURE_SOURCE,
    X_POST_SOURCE,
    X_REPLY_SOURCE,
    TaskService,
)
from roboco.services.x_client import XClient, XMention, XPostResult
from roboco.services.x_post_service import (
    TaskAlreadyCompletedError,
    XPostBodyTooLongError,
    XPostExecuteResult,
    XPostService,
    get_x_post_service,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from uuid import UUID

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SECRETARY_UUID = _foundation.AGENTS["secretary-1"].uuid
ONE = 1
TWO = 2


@contextmanager
def _lock_free() -> Iterator[None]:
    """Patch XPostService's lock helpers so approve/reject exercise the real
    post/cancel path without touching the (test-blocked) Redis."""
    with (
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        yield


class _StubClient(XClient):
    def __init__(self, *, posted: bool = True, tweet_id: str = "999") -> None:
        self._posted = posted
        self._tweet_id = tweet_id
        self.calls: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    async def post_tweet(self, text: str) -> XPostResult:
        self.calls.append(text)
        if not self._posted:
            return XPostResult(posted=False, tweet_id=None, detail="rejected by X")
        return XPostResult(posted=True, tweet_id=self._tweet_id, detail="posted")

    async def fetch_mentions(
        self, since_id: str | None, max_results: int
    ) -> list[XMention]:
        _ = (since_id, max_results)
        return []


async def _seed_draft(
    session: AsyncSession, *, source: str = X_POST_SOURCE, body: str = "Draft body"
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
        title="X draft",
        description=body,
        acceptance_criteria=["CEO approves or rejects"],
        status=TS.PENDING,
        priority=2,
        task_type=TT.ADMINISTRATIVE,
        nature=TN.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        project_id=project.id,
        created_by=SYSTEM_UUID,
        assigned_to=SECRETARY_UUID,
        team=Team.MAIN_PM,
        source=source,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    markers.set_x_draft_body(task, body)
    await session.flush()
    return task


_FEATURE_SLUG = "org-memory"
_FEATURE_TITLE = "Organizational Memory Loop"


async def _seed_feature_draft(
    session: AsyncSession,
    *,
    wants_video: bool = True,
    video_script: str = "",
    body: str = "Draft body",
) -> TaskTable:
    """An X_FEATURE_SOURCE draft carrying the x_feature_ref marker
    ``propose_feature_spotlight`` stamps (Task 4, 2026-07-09 pipeline fixes):
    slug/title always, plus wants_video/video_script when a companion video
    was requested at authoring time."""
    task = await _seed_draft(session, source=X_FEATURE_SOURCE, body=body)
    markers.set_x_feature_ref(
        task,
        {
            "slug": _FEATURE_SLUG,
            "title": _FEATURE_TITLE,
            "wants_video": wants_video,
            "video_script": video_script,
        },
    )
    await session.flush()
    return task


def _enable_video(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "video_engine_enabled", True)
    monkeypatch.setattr(cfg, "video_on_spotlight", True)


def _svc(session: AsyncSession) -> XPostService:
    return get_x_post_service(session)


def _id(task: TaskTable) -> UUID:
    """The ORM id typed as stdlib ``uuid.UUID`` for service-call sites."""
    return cast("UUID", task.id)


@pytest.mark.asyncio
async def test_approve_posts_and_completes(db_session: AsyncSession) -> None:
    task = await _seed_draft(db_session)
    client = _StubClient()
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    assert result.tweet_id == "999"
    assert client.calls == ["Draft body"]
    await db_session.refresh(task)
    assert task.status == TS.COMPLETED
    assert markers.get_x_posted_tweet_id(task) == "999"


@pytest.mark.asyncio
async def test_approve_is_idempotent_second_call_is_noop(
    db_session: AsyncSession,
) -> None:
    task = await _seed_draft(db_session)
    client = _StubClient()
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        svc = _svc(db_session)
        first = await svc.approve(_id(task))
        second = await svc.approve(_id(task))
    assert first is not None
    assert first.status == "posted"
    assert second is not None
    assert second.status == "already_posted"
    assert second.tweet_id == "999"
    # The X client was called exactly once — the second approve never re-posts.
    assert client.calls == ["Draft body"]


@pytest.mark.asyncio
async def test_approve_with_edited_body_posts_the_edit(
    db_session: AsyncSession,
) -> None:
    task = await _seed_draft(db_session, body="Original")
    client = _StubClient()
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        result = await _svc(db_session).approve(_id(task), "Edited body")
    assert result is not None
    assert result.status == "posted"
    assert client.calls == ["Edited body"]


@pytest.mark.asyncio
async def test_approve_rejects_edited_body_over_280_chars(
    db_session: AsyncSession,
) -> None:
    task = await _seed_draft(db_session)
    with pytest.raises(XPostBodyTooLongError):
        await _svc(db_session).approve(_id(task), "x" * 281)


@pytest.mark.asyncio
async def test_approve_no_credentials_result(db_session: AsyncSession) -> None:
    task = await _seed_draft(db_session)
    no_creds_client = _StubClient()

    class _Null(XClient):
        @property
        def configured(self) -> bool:
            return False

        async def post_tweet(self, text: str) -> XPostResult:
            _ = text
            return XPostResult(posted=False, tweet_id=None, detail="no creds")

        async def fetch_mentions(
            self, since_id: str | None, max_results: int
        ) -> list[XMention]:
            _ = (since_id, max_results)
            return []

    _ = no_creds_client
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=_Null()),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "no_credentials"
    await db_session.refresh(task)
    assert task.status == TS.PENDING


@pytest.mark.asyncio
async def test_approve_post_failed_keeps_task_open(db_session: AsyncSession) -> None:
    task = await _seed_draft(db_session)
    client = _StubClient(posted=False)
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "post_failed"
    await db_session.refresh(task)
    assert task.status == TS.PENDING


@pytest.mark.asyncio
async def test_approve_rechecks_completed_under_lock_and_never_reposts(
    db_session: AsyncSession,
) -> None:
    """The double-post guard: a concurrent approve wins the lock, posts, and
    commits COMPLETED after our pre-lock read. Once we acquire the lock, the
    in-lock re-read must see COMPLETED and short-circuit — never re-posting."""
    task = await _seed_draft(db_session)
    client = _StubClient()

    async def _win_the_race(_self: XPostService, _key: str) -> str:
        # Simulate the concurrent winner: the row is COMPLETED (posted) by the
        # time we hold the lock, exactly as the in-lock expire()+re-fetch sees.
        markers.set_x_posted_tweet_id(task, "111")
        task.status = TS.COMPLETED
        await db_session.flush()
        return "tok"

    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", _win_the_race),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "already_posted"
    assert result.tweet_id == "111"
    # The tweet was never posted a second time.
    assert client.calls == []


@pytest.mark.asyncio
async def test_approve_refuses_already_rejected_draft(
    db_session: AsyncSession,
) -> None:
    """The chokepoint guard: approving a CANCELLED (already-rejected) draft
    refuses and never calls the X client — the reproduced bug (a stale
    Approve after reject re-posting)."""
    task = await _seed_draft(db_session)
    with _lock_free():
        await _svc(db_session).reject(_id(task), "not on-brand")
    client = _StubClient()
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "already_rejected"
    assert client.calls == []
    await db_session.refresh(task)
    assert task.status == TS.CANCELLED


@pytest.mark.asyncio
async def test_approve_rechecks_cancelled_under_lock_and_never_posts(
    db_session: AsyncSession,
) -> None:
    """TOCTOU parity with the COMPLETED re-check: a concurrent reject cancels
    the draft after our pre-lock read; the in-lock re-read must see CANCELLED
    and short-circuit — never posting a rejected draft."""
    task = await _seed_draft(db_session)
    client = _StubClient()

    async def _win_the_race(_self: XPostService, _key: str) -> str:
        task.status = TS.CANCELLED
        await db_session.flush()
        return "tok"

    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", _win_the_race),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "already_rejected"
    assert client.calls == []


@pytest.mark.asyncio
async def test_approve_concurrent_lock_held_returns_in_progress(
    db_session: AsyncSession,
) -> None:
    task = await _seed_draft(db_session)
    with patch.object(XPostService, "_acquire_lock", AsyncMock(return_value=None)):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "already_in_progress"


@pytest.mark.asyncio
async def test_approve_unknown_task_returns_none(db_session: AsyncSession) -> None:
    result = await _svc(db_session).approve(uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_reject_records_reason_and_cancels(db_session: AsyncSession) -> None:
    task = await _seed_draft(db_session, source=X_REPLY_SOURCE)
    with _lock_free():
        updated = await _svc(db_session).reject(
            _id(task), "Tone doesn't match our voice"
        )
    assert updated is not None
    assert updated.status == TS.CANCELLED
    assert markers.get_x_reject_reason(updated) == "Tone doesn't match our voice"


@pytest.mark.asyncio
async def test_reject_refused_while_lock_held_by_concurrent_approve(
    db_session: AsyncSession,
) -> None:
    """A concurrent approve holds the post lock (mid-tweet-POST); reject must
    fail closed instead of racing a CANCEL under it — previously reject()
    never even attempted the lock, so it could commit CANCELLED to a draft a
    concurrent approve was about to mark COMPLETED, or clobber the approve's
    outcome depending on commit ordering."""
    task = await _seed_draft(db_session)
    with patch.object(XPostService, "_acquire_lock", AsyncMock(return_value=None)):
        result = await _svc(db_session).reject(_id(task), "not relevant")
    assert result is None
    await db_session.refresh(task)
    assert task.status == TS.PENDING
    assert markers.get_x_reject_reason(task) is None


@pytest.mark.asyncio
async def test_list_open_posts_excludes_terminal(db_session: AsyncSession) -> None:
    open_task = await _seed_draft(db_session)
    rejected_task = await _seed_draft(db_session, source=X_REPLY_SOURCE)
    with _lock_free():
        await _svc(db_session).reject(_id(rejected_task), "not relevant")
    open_posts = await _svc(db_session).list_open_posts()
    ids = {t.id for t in open_posts}
    assert open_task.id in ids
    assert rejected_task.id not in ids


@pytest.mark.asyncio
async def test_approve_posts_feature_spotlight_draft(
    db_session: AsyncSession,
) -> None:
    """The feature-spotlight source rides the same generic post path as
    x_post/x_reply; it only branches for the best-effort video hook below
    (a no-op here since this draft carries no x_feature_ref marker)."""
    task = await _seed_draft(db_session, source=X_FEATURE_SOURCE)
    client = _StubClient()
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    assert result.tweet_id == "999"
    assert client.calls == ["Draft body"]
    await db_session.refresh(task)
    assert task.status == TS.COMPLETED
    assert markers.get_x_posted_tweet_id(task) == "999"


@pytest.mark.asyncio
async def test_list_open_posts_includes_feature_spotlight_source(
    db_session: AsyncSession,
) -> None:
    task = await _seed_draft(db_session, source=X_FEATURE_SOURCE)
    open_posts = await _svc(db_session).list_open_posts()
    ids = {t.id for t in open_posts}
    assert task.id in ids


@pytest.mark.asyncio
async def test_reject_completed_raises(db_session: AsyncSession) -> None:
    """An already-posted (COMPLETED) draft is live on X; rejecting it would
    lie "cancelled (never posted)" while the tweet is public."""
    task = await _seed_draft(db_session)
    markers.set_x_posted_tweet_id(task, "999")
    task.status = TS.COMPLETED
    await db_session.flush()
    with pytest.raises(TaskAlreadyCompletedError):
        await _svc(db_session).reject(_id(task), "nope")


@pytest.mark.asyncio
async def test_reject_concurrent_approve_completes_during_lock_wait(
    db_session: AsyncSession, _test_database_url: str
) -> None:
    """Redis mutex pre-lock write audit regression for ``reject()``: a
    genuinely concurrent approve (a real second session/connection) posts +
    commits COMPLETED in the window between reject's pre-lock read and its
    lock acquisition. The in-lock re-read must see that committed state and
    refuse — the CANCELLED status write and reject reason must never land on
    the just-posted row, proving the fix holds across sessions, not merely
    within one. Mirrors
    ``test_approve_concurrent_edit_does_not_clobber_a_committed_post``."""
    task = await _seed_draft(db_session)
    task_id = _id(task)
    await db_session.commit()

    real_get = TaskService.get
    injected = False

    async def _get_then_inject_concurrent_post(
        self: TaskService, tid: UUID
    ) -> TaskTable | None:
        """Fires once, right after reject's pre-lock read — the exact window
        between that read and reject's own (would-be) pre-lock write."""
        nonlocal injected
        result = await real_get(self, tid)
        if not injected:
            injected = True
            other, other_engine = await _fresh_session(_test_database_url)
            try:
                other_task = await other.get(TaskTable, tid)
                assert other_task is not None
                markers.set_x_posted_tweet_id(other_task, "concurrent-999")
                other_task.status = TS.COMPLETED
                await other.commit()
            finally:
                await _dispose(other, other_engine)
        return result

    with (
        patch.object(TaskService, "get", _get_then_inject_concurrent_post),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
        pytest.raises(TaskAlreadyCompletedError),
    ):
        await _svc(db_session).reject(task_id, "Tone doesn't match")

    fresh, fresh_engine = await _fresh_session(_test_database_url)
    try:
        final = await fresh.get(TaskTable, task_id)
        assert final is not None
        assert final.status == TS.COMPLETED
        assert markers.get_x_posted_tweet_id(final) == "concurrent-999"
        # The reject must never have landed on the just-posted row.
        assert markers.get_x_reject_reason(final) is None
    finally:
        await _dispose(fresh, fresh_engine)


@pytest.mark.asyncio
async def test_list_post_history_excludes_open_drafts(
    db_session: AsyncSession,
) -> None:
    open_task = await _seed_draft(db_session)
    rejected_task = await _seed_draft(db_session, source=X_REPLY_SOURCE)
    with _lock_free():
        await _svc(db_session).reject(_id(rejected_task), "not relevant")
    history = await _svc(db_session).list_post_history()
    ids = {t.id for t in history}
    assert rejected_task.id in ids
    assert open_task.id not in ids


@pytest.mark.asyncio
async def test_list_post_history_newest_acted_first(
    db_session: AsyncSession,
) -> None:
    rejected_task = await _seed_draft(db_session, source=X_REPLY_SOURCE)
    with _lock_free():
        await _svc(db_session).reject(_id(rejected_task), "not relevant")
    posted_task = await _seed_draft(db_session)
    client = _StubClient()
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        await _svc(db_session).approve(_id(posted_task))
    history = await _svc(db_session).list_post_history()
    ids = [t.id for t in history]
    assert ids.index(posted_task.id) < ids.index(rejected_task.id)


@pytest.mark.asyncio
async def test_list_post_history_includes_marker_fields(
    db_session: AsyncSession,
) -> None:
    posted_task = await _seed_draft(db_session)
    client = _StubClient(tweet_id="777")
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        await _svc(db_session).approve(_id(posted_task))
    rejected_task = await _seed_draft(db_session, source=X_REPLY_SOURCE)
    with _lock_free():
        await _svc(db_session).reject(_id(rejected_task), "off-brand tone")

    history = await _svc(db_session).list_post_history()
    by_id = {t.id: t for t in history}
    assert markers.get_x_posted_tweet_id(by_id[posted_task.id]) == "777"
    assert markers.get_x_reject_reason(by_id[rejected_task.id]) == "off-brand tone"


@pytest.mark.asyncio
async def test_list_post_history_respects_limit(db_session: AsyncSession) -> None:
    tasks = []
    for _ in range(3):
        t = await _seed_draft(db_session, source=X_REPLY_SOURCE)
        with _lock_free():
            await _svc(db_session).reject(_id(t), "not relevant")
        tasks.append(t)
    history = await _svc(db_session).list_post_history(limit=2)
    assert len(history) == TWO
    ids = {t.id for t in history}
    assert tasks[2].id in ids
    assert tasks[1].id in ids
    assert tasks[0].id not in ids


@pytest.mark.asyncio
async def test_approve_does_not_flush_edited_body_before_lock(
    db_session: AsyncSession,
) -> None:
    """The CEO's edit must land on the re-read locked row INSIDE the critical
    section, after the COMPLETED check — never on the pre-lock row. A concurrent
    approve that already posted (locked row COMPLETED) must not have this edit
    overwrite the just-posted task's stored body."""
    task = await _seed_draft(db_session, body="Original")
    original_body = markers.get_x_draft_body(task)

    async def _already_posted_locked(
        _self: XPostService, _task_id: UUID, _task: TaskTable, _trimmed: str | None
    ) -> XPostExecuteResult:
        return XPostExecuteResult(
            status="already_posted",
            tweet_id="111",
            detail="this draft was already posted",
        )

    with (
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
        patch.object(XPostService, "_approve_locked", _already_posted_locked),
    ):
        result = await _svc(db_session).approve(_id(task), "new body")
    assert result is not None
    assert result.status == "already_posted"
    await db_session.refresh(task)
    assert markers.get_x_draft_body(task) == original_body


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
async def test_approve_concurrent_edit_does_not_clobber_a_committed_post(
    db_session: AsyncSession, _test_database_url: str
) -> None:
    """Redis mutex pre-lock write audit regression: a genuinely concurrent
    approve (a real second session/connection, not an in-process mock) posts
    + commits COMPLETED in the window between our pre-lock read and our lock
    acquisition. The in-lock re-read must see that committed state and the
    CEO's edited body must never land on the just-posted row — proving the
    fix holds across sessions, not merely within one, mirroring
    VideoPostService's identical cross-session regression test."""
    task = await _seed_draft(db_session, body="Original")
    task_id = _id(task)
    await db_session.commit()

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
                markers.set_x_posted_tweet_id(other_task, "concurrent-999")
                other_task.status = TS.COMPLETED
                await other.commit()
            finally:
                await _dispose(other, other_engine)
        return result

    client = _StubClient()
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(TaskService, "get", _get_then_inject_concurrent_post),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        result = await _svc(db_session).approve(task_id, "Edited body")

    assert result is not None
    assert result.status == "already_posted"
    assert result.tweet_id == "concurrent-999"
    # No double-post: the concurrently-committed tweet wins, ours never fires.
    assert client.calls == []

    fresh, fresh_engine = await _fresh_session(_test_database_url)
    try:
        final = await fresh.get(TaskTable, task_id)
        assert final is not None
        assert final.status == TS.COMPLETED
        assert markers.get_x_posted_tweet_id(final) == "concurrent-999"
        # The edit must never have landed on the just-posted row.
        assert markers.get_x_draft_body(final) == "Original"
    finally:
        await _dispose(fresh, fresh_engine)


# --------------------------------------------------------------------------- #
# Spotlight video hook (Task 4, 2026-07-09 pipeline fixes): moved from
# authoring time (propose_feature_spotlight) to this posted-success branch so
# a ux-dev never burns a cycle on a spotlight the CEO then rejects.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_approve_feature_spotlight_with_video_opens_video_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_video(monkeypatch)
    task = await _seed_feature_draft(db_session, video_script="Custom voiceover script")
    client = _StubClient()
    video_engine = AsyncMock()
    video_engine.open_video_task = AsyncMock(return_value=None)
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=video_engine,
        ),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    video_engine.open_video_task.assert_awaited_once()
    kwargs = video_engine.open_video_task.call_args.kwargs
    assert kwargs["occasion"] == "spotlight org-memory"
    assert kwargs["platforms"] == ["x", "tiktok"]
    assert kwargs["script"] == "Custom voiceover script"
    assert kwargs["brief"] == "Organizational Memory Loop: Draft body"
    # The spotlight's own project scopes the video authoring — without it the
    # video authored against the deployment-anchor project regardless.
    assert kwargs["project_id"] == task.project_id


@pytest.mark.asyncio
async def test_approve_feature_spotlight_video_falls_back_to_brief_script(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No explicit video_script -> script falls back to the brief, mirroring
    the fallback the authoring-time hook used to do."""
    _enable_video(monkeypatch)
    task = await _seed_feature_draft(db_session)
    client = _StubClient()
    video_engine = AsyncMock()
    video_engine.open_video_task = AsyncMock(return_value=None)
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=video_engine,
        ),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    kwargs = video_engine.open_video_task.call_args.kwargs
    expected_brief = "Organizational Memory Loop: Draft body"
    assert kwargs["script"] == expected_brief
    assert kwargs["brief"] == expected_brief


@pytest.mark.asyncio
async def test_approve_feature_spotlight_reapprove_does_not_reopen_video(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotent re-approve: the second call short-circuits on the already-
    COMPLETED check before ever reaching _post/_open_spotlight_video again."""
    _enable_video(monkeypatch)
    task = await _seed_feature_draft(db_session)
    client = _StubClient()
    video_engine = AsyncMock()
    video_engine.open_video_task = AsyncMock(return_value=None)
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=video_engine,
        ),
    ):
        svc = _svc(db_session)
        first = await svc.approve(_id(task))
        second = await svc.approve(_id(task))
    assert first is not None
    assert first.status == "posted"
    assert second is not None
    assert second.status == "already_posted"
    video_engine.open_video_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_plain_x_post_never_opens_video(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain x_post draft carries no x_feature_ref, so the source check
    alone keeps the video hook from ever firing for it."""
    _enable_video(monkeypatch)
    task = await _seed_draft(db_session, source=X_POST_SOURCE)
    client = _StubClient()
    video_engine = AsyncMock()
    video_engine.open_video_task = AsyncMock(return_value=None)
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=video_engine,
        ),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    video_engine.open_video_task.assert_not_called()


@pytest.mark.asyncio
async def test_reject_feature_spotlight_with_wants_video_opens_none(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rejecting a spotlight draft never posts, so the video hook (which only
    fires from the posted-success branch of _post) never runs either."""
    _enable_video(monkeypatch)
    task = await _seed_feature_draft(db_session)
    video_engine = AsyncMock()
    video_engine.open_video_task = AsyncMock(return_value=None)
    with (
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=video_engine,
        ),
        _lock_free(),
    ):
        updated = await _svc(db_session).reject(_id(task), "not on-brand")
    assert updated is not None
    assert updated.status == TS.CANCELLED
    video_engine.open_video_task.assert_not_called()


@pytest.mark.asyncio
async def test_approve_feature_spotlight_video_flags_off_skips(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "video_engine_enabled", False)
    monkeypatch.setattr(cfg, "video_on_spotlight", False)
    task = await _seed_feature_draft(db_session)
    client = _StubClient()
    video_engine = AsyncMock()
    video_engine.open_video_task = AsyncMock(return_value=None)
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=video_engine,
        ),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    video_engine.open_video_task.assert_not_called()


@pytest.mark.asyncio
async def test_approve_feature_spotlight_without_wants_video_skips(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flags on but the draft's author didn't request a video (wants_video
    absent/False on the marker) -> no video task, distinct from the
    flags-off case above."""
    _enable_video(monkeypatch)
    task = await _seed_feature_draft(db_session, wants_video=False)
    client = _StubClient()
    video_engine = AsyncMock()
    video_engine.open_video_task = AsyncMock(return_value=None)
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
        patch(
            "roboco.services.video_engine.get_video_engine",
            return_value=video_engine,
        ),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    video_engine.open_video_task.assert_not_called()


@pytest.mark.asyncio
async def test_approve_feature_spotlight_video_failure_does_not_break_post(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort: a video-engine blow-up must not affect the already-
    succeeded post."""
    _enable_video(monkeypatch)
    task = await _seed_feature_draft(db_session)
    client = _StubClient()
    with (
        patch("roboco.services.x_post_service.build_x_client", return_value=client),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
        patch(
            "roboco.services.video_engine.get_video_engine",
            side_effect=RuntimeError("video-engine boom"),
        ),
    ):
        result = await _svc(db_session).approve(_id(task))
    assert result is not None
    assert result.status == "posted"
    await db_session.refresh(task)
    assert task.status == TS.COMPLETED
