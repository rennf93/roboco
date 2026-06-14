"""GitService PR-divergence primitives: rebase_onto_base + close_pull_request.

These back both the sequence-ordered merge (rebase a later sibling onto the
prior one's merged result) and the conflict resolver (rebase a wedged PR,
then close-if-superseded / re-merge / escalate). The classification a rebase
yields — superseded vs rebased vs conflicts — drives the whole resolution, so
each branch is pinned here against a mocked git.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.git import GitService


def _git_service() -> GitService:
    return GitService.__new__(GitService)


def _result(returncode: int = 0, stdout: str = "") -> Any:
    return type("R", (), {"returncode": returncode, "stdout": stdout})()


_HEAD = "feature/frontend/root--cell--leaf"
_BASE = "feature/frontend/root--cell"


@pytest.mark.asyncio
async def test_rebase_superseded_when_no_unique_commits() -> None:
    """Clean rebase + zero commits ahead of base => superseded (safe to close)."""
    svc = _git_service()
    pushed: list[list[str]] = []

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        if args[0] == "push":
            pushed.append(args)
        if args[:2] == ["rev-list", "--count"]:
            return _result(stdout="0\n")
        return _result()

    with patch.object(svc, "_run_git", new=fake_run):
        out = await svc.rebase_onto_base(
            Path("/tmp/ws"), head_branch=_HEAD, base_branch=_BASE, git_token="tok"
        )
    assert out == {"status": "superseded"}
    # A superseded branch must NOT be force-pushed — nothing changed.
    assert pushed == []


@pytest.mark.asyncio
async def test_rebase_rebased_force_pushes_when_unique_commits() -> None:
    """Clean rebase + commits ahead of base => rebased + force-push the head."""
    svc = _git_service()
    pushed: list[list[str]] = []

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        if args[0] == "push":
            pushed.append(args)
            return _result()
        if args[:2] == ["rev-list", "--count"]:
            return _result(stdout="3\n")
        return _result()

    with patch.object(svc, "_run_git", new=fake_run):
        out = await svc.rebase_onto_base(
            Path("/tmp/ws"), head_branch=_HEAD, base_branch=_BASE, git_token="tok"
        )
    assert out == {"status": "rebased", "unique_commits": 3}
    # Only the head branch is force-pushed, with lease, never the base.
    assert pushed == [["push", "--force-with-lease", "origin", f"HEAD:{_HEAD}"]]


@pytest.mark.asyncio
async def test_rebase_conflicts_aborts_and_reports_files() -> None:
    """A failed rebase is aborted and the conflicting files reported."""
    svc = _git_service()
    aborted = False

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        nonlocal aborted
        if args == ["rebase", f"origin/{_BASE}"]:
            return _result(returncode=1)
        if args[:2] == ["diff", "--name-only"]:
            return _result(stdout="src/a.tsx\nsrc/b.tsx\n")
        if args == ["rebase", "--abort"]:
            aborted = True
            return _result()
        return _result()

    with patch.object(svc, "_run_git", new=fake_run):
        out = await svc.rebase_onto_base(
            Path("/tmp/ws"), head_branch=_HEAD, base_branch=_BASE, git_token="tok"
        )
    assert out == {"status": "conflicts", "files": ["src/a.tsx", "src/b.tsx"]}
    assert aborted is True


@pytest.mark.asyncio
async def test_rebase_never_force_pushes_on_conflict() -> None:
    """Guard: the destructive force-push must not fire when a rebase conflicts."""
    svc = _git_service()
    pushed: list[list[str]] = []

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        if args[0] == "push":
            pushed.append(args)
        if args == ["rebase", f"origin/{_BASE}"]:
            return _result(returncode=1)
        if args[:2] == ["diff", "--name-only"]:
            return _result(stdout="")
        return _result()

    with patch.object(svc, "_run_git", new=fake_run):
        await svc.rebase_onto_base(
            Path("/tmp/ws"), head_branch=_HEAD, base_branch=_BASE, git_token="tok"
        )
    assert pushed == []


@pytest.mark.asyncio
async def test_close_pull_request_patches_state_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """close_pull_request issues a PATCH state=closed (and an optional comment)."""
    svc = _git_service()
    # Stub the task/project/workspace/token/remote resolution chain via
    # monkeypatch.setattr (not direct assignment) so mypy's method-assign check
    # stays satisfied without silencing it.
    task = type("T", (), {"id": "t", "assigned_to": None, "created_by": None})()
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=type("Res", (), {"scalar_one_or_none": lambda _self: task})()
    )
    delete_branch = AsyncMock()
    monkeypatch.setattr(svc, "session", session, raising=False)
    monkeypatch.setattr(
        svc,
        "_project_for_task",
        AsyncMock(return_value=type("P", (), {"slug": "proj"})()),
    )
    monkeypatch.setattr(
        svc, "_resolve_workspace_agent_id", MagicMock(return_value=None)
    )
    monkeypatch.setattr(svc, "get_workspace", AsyncMock(return_value=Path("/tmp/ws")))
    monkeypatch.setattr(
        svc, "_get_project_token_or_raise", AsyncMock(return_value="tok")
    )
    monkeypatch.setattr(
        svc, "_parse_github_remote", MagicMock(return_value=("owner", "repo"))
    )
    monkeypatch.setattr(svc, "_delete_pr_branch_best_effort", delete_branch)

    calls: list[tuple[str, str]] = []

    class _Resp:
        is_success = True
        status_code = 200
        text = ""

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def post(self, url: str, **_kw: Any) -> _Resp:
            calls.append(("POST", url))
            return _Resp()

        async def patch(self, url: str, **_kw: Any) -> _Resp:
            calls.append(("PATCH", url))
            return _Resp()

    with patch("roboco.services.git.httpx.AsyncClient", return_value=_Client()):
        await svc.close_pull_request(159, comment="superseded by #158")

    assert (
        "POST",
        "https://api.github.com/repos/owner/repo/issues/159/comments",
    ) in calls
    assert ("PATCH", "https://api.github.com/repos/owner/repo/pulls/159") in calls
    delete_branch.assert_awaited_once()
