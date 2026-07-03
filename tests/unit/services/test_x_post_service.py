"""XPostService coverage: approve posts (idempotent), reject cancels.

Mirrors the release-proposal service tests. The Redis lock helpers are
patched (no live Redis in tests, matching the project's ``_no_live_redis``
fixture) so approve exercises the real post + status-transition path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch
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
from roboco.services.task import X_POST_SOURCE, X_REPLY_SOURCE
from roboco.services.x_client import XClient, XMention, XPostResult
from roboco.services.x_post_service import (
    XPostBodyTooLongError,
    XPostService,
    get_x_post_service,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
SECRETARY_UUID = _foundation.AGENTS["secretary-1"].uuid
ONE = 1


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
    assert task.status == TS.PENDING  # never advanced without credentials


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
    updated = await _svc(db_session).reject(_id(task), "Tone doesn't match our voice")
    assert updated is not None
    assert updated.status == TS.CANCELLED
    assert markers.get_x_reject_reason(updated) == "Tone doesn't match our voice"


@pytest.mark.asyncio
async def test_list_open_posts_excludes_terminal(db_session: AsyncSession) -> None:
    open_task = await _seed_draft(db_session)
    rejected_task = await _seed_draft(db_session, source=X_REPLY_SOURCE)
    await _svc(db_session).reject(_id(rejected_task), "not relevant")
    open_posts = await _svc(db_session).list_open_posts()
    ids = {t.id for t in open_posts}
    assert open_task.id in ids
    assert rejected_task.id not in ids
