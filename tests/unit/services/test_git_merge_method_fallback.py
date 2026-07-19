"""GitService falls back to a permitted merge method when the repo refuses one.

A repo with squash merges disabled returns 405 ("Squash merges are not allowed")
on the default squash merge. Without a fallback the PM strands on an open,
mergeable PR — code built, QA passed, docs written, one API call from done.
merge_pull_request must look up a method the repo permits and retry once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import roboco.services.git as git_module
from roboco.exceptions import GitError
from roboco.services.forge import RepoRef
from roboco.services.git import GitService


def _git_service() -> GitService:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    return svc


def _resp(
    status_code: int, *, is_success: bool, json_data: dict[str, Any] | None = None
) -> Any:
    payload = json_data or {}
    return type(
        "R",
        (),
        {
            "status_code": status_code,
            "is_success": is_success,
            "text": "",
            "json": lambda _self=None, _p=payload: _p,
        },
    )()


class _FakeClient:
    def __init__(self, resp: Any) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    async def get(self, _url: str, **_kwargs: Any) -> Any:
        return self._resp


@pytest.mark.asyncio
async def test_first_allowed_skips_disabled_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _git_service()
    resp = _resp(
        200,
        is_success=True,
        json_data={
            "allow_squash_merge": False,
            "allow_merge_commit": True,
            "allow_rebase_merge": True,
        },
    )
    monkeypatch.setattr(
        git_module.httpx, "AsyncClient", lambda *_a, **_k: _FakeClient(resp)
    )
    method = await svc._first_allowed_merge_method(
        RepoRef("o", "r"), "tok", exclude="squash"
    )
    assert method == "merge"  # squash disabled + excluded -> next permitted


@pytest.mark.asyncio
async def test_first_allowed_returns_none_when_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _git_service()
    resp = _resp(403, is_success=False)
    monkeypatch.setattr(
        git_module.httpx, "AsyncClient", lambda *_a, **_k: _FakeClient(resp)
    )
    assert await svc._first_allowed_merge_method(RepoRef("o", "r"), "tok") is None


@pytest.mark.asyncio
async def test_merge_retries_with_allowed_method_on_405(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _git_service()
    monkeypatch.setattr(
        svc, "_get_project_token_or_raise", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(
        svc, "_parse_github_remote", lambda _ws: RepoRef("owner", "repo")
    )
    monkeypatch.setattr(
        svc, "_delete_pr_branch_best_effort", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        svc, "_project_default_branch", AsyncMock(return_value="master")
    )
    monkeypatch.setattr(svc, "_sync_target_branch", AsyncMock(return_value="abc123"))
    monkeypatch.setattr(
        svc, "_first_allowed_merge_method", AsyncMock(return_value="merge")
    )

    calls: list[str] = []

    async def fake_call(_repo_ref: RepoRef, _pr: int, _token: str, method: str) -> Any:
        calls.append(method)
        return (
            _resp(200, is_success=True)
            if method == "merge"
            else _resp(405, is_success=False)
        )

    monkeypatch.setattr(svc, "_call_merge_api", fake_call)

    target, commit = await svc.merge_pull_request(Path("/tmp/ws"), 7, "squash", "proj")

    assert calls == ["squash", "merge"]  # refused squash, retried with merge
    assert target == "master"
    assert commit == "abc123"


@pytest.mark.asyncio
async def test_merge_does_not_retry_when_method_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _git_service()
    monkeypatch.setattr(
        svc, "_get_project_token_or_raise", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(
        svc, "_parse_github_remote", lambda _ws: RepoRef("owner", "repo")
    )
    monkeypatch.setattr(
        svc, "_delete_pr_branch_best_effort", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        svc, "_project_default_branch", AsyncMock(return_value="master")
    )
    monkeypatch.setattr(svc, "_sync_target_branch", AsyncMock(return_value="abc123"))
    lookup = AsyncMock(return_value="merge")
    monkeypatch.setattr(svc, "_first_allowed_merge_method", lookup)

    calls: list[str] = []

    async def fake_call(_repo_ref: RepoRef, _pr: int, _token: str, method: str) -> Any:
        calls.append(method)
        return _resp(200, is_success=True)

    monkeypatch.setattr(svc, "_call_merge_api", fake_call)

    await svc.merge_pull_request(Path("/tmp/ws"), 7, "squash", "proj")

    assert calls == ["squash"]  # allowed first time, no second call
    lookup.assert_not_awaited()  # fallback lookup never triggered


@pytest.mark.asyncio
async def test_merge_already_merged_pr_is_idempotent_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CEO ``merge_pull_request`` path treats an already-merged PR as
    idempotent success (not GitError) — a merge PUT on an already-merged PR
    returns the same 405 as a genuine refusal, so they must be disambiguated.
    """

    svc = _git_service()
    monkeypatch.setattr(
        svc, "_get_project_token_or_raise", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(
        svc, "_parse_github_remote", lambda _ws: RepoRef("owner", "repo")
    )
    delete_branch = AsyncMock(return_value=None)
    monkeypatch.setattr(svc, "_delete_pr_branch_best_effort", delete_branch)
    monkeypatch.setattr(
        svc, "_project_default_branch", AsyncMock(return_value="master")
    )
    sync_target = AsyncMock(return_value="abc123")
    monkeypatch.setattr(svc, "_sync_target_branch", sync_target)
    # No fallback retry would help (already merged => 405 either way); make the
    # fallback lookup return None so the code falls straight through to the
    # already-merged disambiguation.
    monkeypatch.setattr(
        svc, "_first_allowed_merge_method", AsyncMock(return_value=None)
    )
    # The GitHub merge PUT "refuses" (405) because the PR is already merged.
    monkeypatch.setattr(
        svc, "_call_merge_api", AsyncMock(return_value=_resp(405, is_success=False))
    )
    # ... and the PR is in fact already merged on GitHub.
    monkeypatch.setattr(svc, "_pr_is_merged", AsyncMock(return_value=True))

    target, commit = await svc.merge_pull_request(Path("/tmp/ws"), 42, "squash", "proj")

    # Idempotent success, not a raise.
    assert target == "master"
    assert commit == "abc123"
    # The post-merge steps still run (branch cleanup, target sync) so the
    # caller's state stays consistent with "the PR is merged".
    delete_branch.assert_awaited_once()
    sync_target.assert_awaited_once()


@pytest.mark.asyncio
async def test_merge_raises_when_not_merged_and_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine merge refusal (not mergeable, NOT already-merged) still raises
    GitError — the idempotency guard must not mask a real failure."""

    svc = _git_service()
    monkeypatch.setattr(
        svc, "_get_project_token_or_raise", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(
        svc, "_parse_github_remote", lambda _ws: RepoRef("owner", "repo")
    )
    delete_branch = AsyncMock(return_value=None)
    monkeypatch.setattr(svc, "_delete_pr_branch_best_effort", delete_branch)
    monkeypatch.setattr(
        svc, "_project_default_branch", AsyncMock(return_value="master")
    )
    monkeypatch.setattr(svc, "_sync_target_branch", AsyncMock(return_value="abc123"))
    monkeypatch.setattr(
        svc, "_first_allowed_merge_method", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        svc, "_call_merge_api", AsyncMock(return_value=_resp(405, is_success=False))
    )
    # PR is NOT merged — this is a real conflict, not an idempotent retry.
    monkeypatch.setattr(svc, "_pr_is_merged", AsyncMock(return_value=False))

    with pytest.raises(GitError):
        await svc.merge_pull_request(Path("/tmp/ws"), 42, "squash", "proj")

    # No post-merge cleanup ran — the merge did not land.
    delete_branch.assert_not_awaited()
