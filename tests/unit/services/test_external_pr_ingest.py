"""External-PR review dedup — review once per (project, PR, head commit).

``external_review_task_exists`` drives re-review off the PR's head SHA, stored as
the ``external_pr_head`` orchestration marker (migration 041); dismissal is the
``dismissed`` marker. An unchanged PR (same head) is skipped, new commits open a
fresh review, and legacy/unknown-SHA tasks are never re-reviewed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.services.task import TaskService


def _service(scalar_rows: list[object]) -> TaskService:
    """A TaskService whose next query returns these scalar rows."""
    res = MagicMock()
    res.scalars.return_value.all.return_value = scalar_rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=res)
    session.flush = AsyncMock()
    return TaskService(session)


def _markers(head: str | None = None, dismissed: bool = False) -> dict:
    om: dict = {}
    if head is not None:
        om["external_pr_head"] = head
    if dismissed:
        om["dismissed"] = True
    return om


def _bind(svc: TaskService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


# ---------------------------------------------------------------------------
# external_review_task_exists — scalars are orchestration_markers dicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_task_yet_ingests() -> None:
    svc = _service([])
    assert await svc.external_review_task_exists(uuid4(), 170, "abc") is False


@pytest.mark.asyncio
async def test_same_head_sha_skips() -> None:
    svc = _service([_markers("abc")])
    assert await svc.external_review_task_exists(uuid4(), 170, "abc") is True


@pytest.mark.asyncio
async def test_new_head_sha_rereviews() -> None:
    # PR got new commits since the last review → open a fresh review.
    svc = _service([_markers("abc")])
    assert await svc.external_review_task_exists(uuid4(), 170, "def") is False


@pytest.mark.asyncio
async def test_legacy_markerless_task_not_rereviewed() -> None:
    # A task ingested before head-SHA tracking existed → don't re-review it.
    svc = _service([None])
    assert await svc.external_review_task_exists(uuid4(), 170, "def") is True


@pytest.mark.asyncio
async def test_unknown_head_sha_does_not_spam() -> None:
    # Can't detect change (no SHA from GitHub) → treat as reviewed.
    svc = _service([_markers("abc")])
    assert await svc.external_review_task_exists(uuid4(), 170, None) is True


@pytest.mark.asyncio
async def test_multiple_old_shas_still_rereviews_new() -> None:
    svc = _service([_markers("abc"), _markers("def")])
    assert await svc.external_review_task_exists(uuid4(), 170, "ghi") is False
    assert await svc.external_review_task_exists(uuid4(), 170, "def") is True


# ---------------------------------------------------------------------------
# list queues — post-query dismissed filter (scalars are task objects)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_awaiting_decision_excludes_dismissed() -> None:
    pending = SimpleNamespace(orchestration_markers=_markers("abc"))
    dismissed = SimpleNamespace(orchestration_markers=_markers("def", dismissed=True))
    svc = _service([pending, dismissed])
    out = await svc.list_external_pr_reviews_awaiting_decision()
    assert out == [pending]


@pytest.mark.asyncio
async def test_list_external_pr_reviews_excludes_dismissed() -> None:
    reviewing = SimpleNamespace(orchestration_markers=_markers("abc"))
    dismissed = SimpleNamespace(orchestration_markers=_markers("def", dismissed=True))
    svc = _service([reviewing, dismissed])
    out = await svc.list_external_pr_reviews()
    assert out == [reviewing]


@pytest.mark.asyncio
async def test_dismiss_marks_and_is_idempotent() -> None:
    task = SimpleNamespace(source="external_pr", orchestration_markers=_markers("abc"))
    svc = _service([])
    _bind(svc, "get", AsyncMock(return_value=task))
    await svc.dismiss_external_pr_review(uuid4())
    assert markers.is_dismissed(task) is True
    await svc.dismiss_external_pr_review(uuid4())  # idempotent
    assert markers.is_dismissed(task) is True
    assert task.orchestration_markers["dismissed"] is True


@pytest.mark.asyncio
async def test_dismiss_rejects_non_external_pr() -> None:
    task = MagicMock(source="code")
    session = MagicMock()
    session.flush = AsyncMock()
    svc = TaskService(session)
    _bind(svc, "get", AsyncMock(return_value=task))
    assert await svc.dismiss_external_pr_review(uuid4()) is None


# ---------------------------------------------------------------------------
# active_task_owns_branch — the internal-PR "is this a lifecycle PR?" check
# ---------------------------------------------------------------------------


def _branch_service(*, found: bool) -> TaskService:
    res = MagicMock()
    res.first.return_value = ("some-task-id",) if found else None
    session = MagicMock()
    session.execute = AsyncMock(return_value=res)
    return TaskService(session)


@pytest.mark.asyncio
async def test_active_task_owns_branch_true_when_live_task_holds_it() -> None:
    assert (
        await _branch_service(found=True).active_task_owns_branch("feature/x", uuid4())
        is True
    )


@pytest.mark.asyncio
async def test_active_task_owns_branch_false_when_no_live_task() -> None:
    svc = _branch_service(found=False)
    assert await svc.active_task_owns_branch("feature/x", uuid4()) is False


@pytest.mark.asyncio
async def test_active_task_owns_branch_false_for_empty_branch() -> None:
    # No branch → cannot be owned; never hits the DB.
    assert await TaskService(MagicMock()).active_task_owns_branch("", uuid4()) is False
