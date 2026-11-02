"""GitService.create_pr adopts the PR Entire opened for a linked branch.

On a project bridged to Entire trails, an integration branch is linked to
its trail right before its PR (``link_before_pr``); a branch that already
has commits then gets a draft PR from Entire, so RoboCo's create hits
GitHub's 422 "already exists". The adoption sets RoboCo's title and body,
keeping Entire's markers, and takes the PR out of draft (GitHub never merges
a draft) before RoboCo records it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.exceptions import GitError
from roboco.services import git as git_module
from roboco.services.forge import RepoRef
from roboco.services.git import GitService

_LINK = (
    "<!-- entire-trail-link-start -->\n"
    "https://entire.com/gh/acme/-/trails/9/changes/app/4\n"
    "<!-- entire-trail-link-end -->"
)
_ENTIRE_BODY = (
    f"{_LINK}\n\nThis draft pull request was opened by [Entire](https://entire.com)"
    " to back the linked trail.\n\n<!-- entire-shadow-pr -->"
)
_BRANCH = "feature/backend/root1234--cell5678"
_BASE = "feature/main_pm/root1234"
_REF = RepoRef("acme", "app")


_OK = 200
_UNPROCESSABLE = 422
_FIRST_NON_SUCCESS = 300


@dataclass
class _Harness:
    svc: GitService
    task: Any
    project: Any
    forge: Any
    post_pr: AsyncMock
    record_pr: AsyncMock


def _response(status: int, payload: Any = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.is_success = status < _FIRST_NON_SUCCESS
    resp.json.return_value = payload if payload is not None else {}
    resp.text = text
    return resp


def _harness(
    monkeypatch: pytest.MonkeyPatch, found: dict[str, Any], *, opted_in: bool = True
) -> _Harness:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    task = MagicMock(
        id=uuid4(), title="Rate limiting", description="RoboCo body", team="backend"
    )
    task.batch_id = None
    project = MagicMock(slug="app", entire_trails_enabled=opted_in)
    forge = MagicMock()
    forge.update_pr = AsyncMock(return_value=_response(_OK))
    forge.mark_pr_ready_for_review = AsyncMock(
        return_value=_response(_OK, {"data": {"markPullRequestReadyForReview": {}}})
    )
    post_pr = AsyncMock(
        return_value=_response(
            _UNPROCESSABLE, text='{"message": "A pull request already exists"}'
        )
    )
    record_pr = AsyncMock()
    monkeypatch.setattr(git_module, "ForgeRouter", lambda: forge)
    for name, value in {
        "_task_for_branch": AsyncMock(return_value=task),
        "_project_for_task": AsyncMock(return_value=project),
        "_workspace_for_branch": AsyncMock(return_value=Path("/tmp/ws")),
        "_get_project_token_or_raise": AsyncMock(return_value="tok"),
        "_repo_ref_for_workspace": AsyncMock(return_value=_REF),
        "_ensure_base_on_remote": AsyncMock(return_value=_BASE),
        "_post_pr": post_pr,
        "_find_existing_pr": AsyncMock(return_value=found),
        "_record_pr_atomically": record_pr,
        "_apply_pr_labels": AsyncMock(),
    }.items():
        object.__setattr__(svc, name, value)
    return _Harness(svc, task, project, forge, post_pr, record_pr)


def _found(*, body: str = _ENTIRE_BODY, draft: bool = True) -> dict[str, Any]:
    return {
        "number": 77,
        "html_url": "https://github.com/acme/app/pull/77",
        "title": _BRANCH,
        "body": body,
        "draft": draft,
        "node_id": "PR_kwDOabc",
    }


@pytest.fixture
def link(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(settings, "entire_trails_enabled", True)
    linker = AsyncMock()
    monkeypatch.setattr(git_module, "link_before_pr", linker)
    return linker


async def test_an_entire_draft_pr_becomes_roboco_s_and_leaves_draft(
    link: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _harness(monkeypatch, _found())

    out = await h.svc.create_pr(_BRANCH, parent=_BASE, is_root_pr=False)

    assert out["pr_number"] == _found()["number"]
    link.assert_awaited_once_with(h.task.id, h.project, branch=_BRANCH, base=_BASE)
    payload = h.forge.update_pr.await_args.kwargs["payload"]
    assert payload["title"] == f"[{str(h.task.id)[:8]}] Rate limiting"
    assert payload["body"] == f"{_LINK}\n\nRoboCo body\n\n<!-- entire-shadow-pr -->"
    h.forge.mark_pr_ready_for_review.assert_awaited_once_with(_REF, "tok", "PR_kwDOabc")
    h.record_pr.assert_awaited_once()


async def test_a_ready_entire_pr_only_gets_roboco_s_title_and_body(
    link: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = link
    h = _harness(monkeypatch, _found(draft=False))

    await h.svc.create_pr(_BRANCH, parent=_BASE, is_root_pr=False)

    h.forge.update_pr.assert_awaited_once()
    h.forge.mark_pr_ready_for_review.assert_not_awaited()


async def test_a_pr_entire_did_not_mark_is_left_alone(
    link: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = link
    h = _harness(monkeypatch, _found(body="someone else's body", draft=True))

    await h.svc.create_pr(_BRANCH, parent=_BASE, is_root_pr=False)

    h.forge.update_pr.assert_not_awaited()
    h.forge.mark_pr_ready_for_review.assert_not_awaited()


@pytest.mark.parametrize("unbridged", ["flag_off", "project_not_opted_in"])
async def test_nothing_is_adopted_unless_the_project_is_bridged(
    link: AsyncMock, monkeypatch: pytest.MonkeyPatch, unbridged: str
) -> None:
    _ = link
    if unbridged == "flag_off":
        monkeypatch.setattr(settings, "entire_trails_enabled", False)
    h = _harness(monkeypatch, _found(), opted_in=unbridged != "project_not_opted_in")

    await h.svc.create_pr(_BRANCH, parent=_BASE, is_root_pr=False)

    h.forge.update_pr.assert_not_awaited()
    h.forge.mark_pr_ready_for_review.assert_not_awaited()


async def test_a_refused_un_draft_fails_before_the_pr_is_recorded(
    link: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GraphQL reports a refused mutation as 200 with errors; the verb fails
    and its retry adopts the PR again."""
    _ = link
    h = _harness(monkeypatch, _found())
    h.forge.mark_pr_ready_for_review = AsyncMock(
        return_value=_response(_OK, {"errors": [{"message": "not permitted"}]})
    )

    with pytest.raises(GitError, match="out of draft"):
        await h.svc.create_pr(_BRANCH, parent=_BASE, is_root_pr=False)

    h.record_pr.assert_not_awaited()


async def test_the_link_runs_before_the_pr_is_posted(
    link: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _harness(monkeypatch, _found())
    order = MagicMock()
    order.attach_mock(link, "link")
    order.attach_mock(h.post_pr, "post")

    await h.svc.create_pr(_BRANCH, parent=_BASE, is_root_pr=False)

    assert [name for name, _args, _kwargs in order.mock_calls][:2] == ["link", "post"]
