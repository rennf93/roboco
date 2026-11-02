"""A review GitHub refuses to take is reported to the CEO as not posted.

post_pr_review completes the review task, then posts the review to the PR and
tells the CEO it was reviewed. When GitHub refused the post (2026-10-28: 422
"Issue is locked"; 2026-11-01: 403 "Resource not accessible by integration",
the token lacks pull-request write on PR #178's repo) the CEO still got
"reviewed and a change-request posted", so nobody knew the contributor never
saw it. The notification now says the post failed and why; the review stays
saved on the task.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.git import GitError
from roboco.services.notification import NotificationService

_REFUSED = (
    'GitHub API refused PR review (403): {"message":"Resource not accessible '
    'by integration"}'
)


def _choreographer(git: Any) -> Any:
    deps: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": git,
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    return Choreographer(ChoreographerDeps(**deps))


async def _side_effects(git: Any) -> dict[str, Any]:
    notifications = MagicMock()
    notifications.send_external_pr_reviewed_notification = AsyncMock()
    task = MagicMock(pr_url="https://github.com/acme/repo/pull/178", title="PR 178")
    with patch(
        "roboco.services.notification.NotificationService",
        return_value=notifications,
    ):
        await _choreographer(git)._post_review_side_effects(
            t=task,
            slug="acme",
            pr_number=178,
            post_body="review",
            event="REQUEST_CHANGES",
            task_id=uuid4(),
        )
    notify = notifications.send_external_pr_reviewed_notification
    notify.assert_awaited_once()
    assert notify.await_args is not None
    return dict(notify.await_args.kwargs)


@pytest.mark.asyncio
async def test_a_refused_post_reaches_the_ceo_with_the_reason() -> None:
    git = AsyncMock()
    git.post_pr_review = AsyncMock(side_effect=GitError(_REFUSED, {}))
    sent = await _side_effects(git)
    assert "403" in sent["post_error"]


@pytest.mark.asyncio
async def test_a_posted_review_reports_no_error() -> None:
    git = AsyncMock()
    git.post_pr_review = AsyncMock(return_value={"id": 1})
    sent = await _side_effects(git)
    assert sent["post_error"] is None


async def _notification(post_error: str | None) -> Any:
    service = NotificationService()
    created = AsyncMock()
    with patch.object(service, "_create_notification", created):
        await service.send_external_pr_reviewed_notification(
            task_id=str(uuid4()),
            pr_number=178,
            pr_url="https://github.com/acme/repo/pull/178",
            task_title="PR 178",
            post_error=post_error,
        )
    assert created.await_args is not None
    return created.await_args.args[0]


@pytest.mark.asyncio
async def test_the_ceo_is_told_a_refused_review_was_not_posted() -> None:
    params = await _notification(_REFUSED)
    assert "not posted" in params.subject.lower()
    assert "Resource not accessible by integration" in params.body
    assert "change-request posted" not in params.body


@pytest.mark.asyncio
async def test_the_ceo_is_told_a_posted_review_was_posted() -> None:
    params = await _notification(None)
    assert "change-request posted" in params.body
