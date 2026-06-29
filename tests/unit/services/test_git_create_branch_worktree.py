"""create_branch cuts a per-task worktree, not a shared-clone checkout (F123, Phase B).

The old flow ``reset --hard`` + ``checkout <base>`` + ``merge --ff-only`` +
``checkout -b`` ran on the ONE shared clone — so a coordinator PM claiming a
second root clobbered the first root's working tree. The new flow delegates to
``WorkspaceService.ensure_worktree`` (``git worktree add`` under
``{clone_root}/.worktrees/{task-short}/``) and pushes from the clone root. The
shared clone's HEAD is never moved by a claim.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.api.schemas.git import GitCreateBranchRequest
from roboco.services.git import GitService


def _service() -> GitService:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    return svc


def _stub_base(svc: GitService) -> None:
    object.__setattr__(svc, "_resolve_base_branch", AsyncMock(return_value="master"))
    object.__setattr__(svc, "_project_default_branch", AsyncMock(return_value="master"))
    object.__setattr__(svc, "_token_for_project", AsyncMock(return_value=None))


def _req(task_id: UUID) -> GitCreateBranchRequest:
    return GitCreateBranchRequest(
        project_slug="roboco-api",
        task_id=task_id,
        branch_type="feature",
        parent_branch=None,
    )


async def _drive(
    svc: GitService, task_id: UUID, unique_commits: str
) -> tuple[object, list[tuple[Path, list[str]]], list[tuple], str]:
    """Run create_branch recording (_run_git calls, ensure_worktree calls)."""
    calls: list[tuple[Path, list[str]]] = []

    async def fake_run_git(workspace: Path, args: list[str], **_kw: object) -> object:
        calls.append((Path(workspace), list(args)))
        if args[:2] == ["rev-list", "--count"]:
            return MagicMock(stdout=f"{unique_commits}\n", returncode=0)
        # ls-remote / rev-parse / fetch / push all "succeed".
        return MagicMock(stdout="abc\trefs/heads/master\n", returncode=0)

    object.__setattr__(svc, "_run_git", fake_run_git)

    ensure_calls: list[tuple] = []
    ws_svc = MagicMock()
    ws_svc.ensure_worktree = AsyncMock(
        side_effect=lambda clone_root, worktree, branch, base: ensure_calls.append(
            (Path(clone_root), Path(worktree), branch, base)
        )
    )

    branch = "feature/backend/abc12345--def67890"
    with (
        patch("roboco.services.git.build_branch_name", AsyncMock(return_value=branch)),
        patch(
            "roboco.services.git.get_task_service",
            MagicMock(return_value=MagicMock(update=AsyncMock())),
        ),
        patch(
            "roboco.services.git.get_workspace_service", MagicMock(return_value=ws_svc)
        ),
    ):
        out = await svc.create_branch(Path("/tmp/ws"), "backend", _req(task_id))
    return out, calls, ensure_calls, branch


@pytest.mark.asyncio
async def test_create_branch_does_not_reset_or_checkout_shared_clone() -> None:
    # THE F123 assertion: a claim never mutates the shared clone's working tree.
    svc = _service()
    _stub_base(svc)
    _, calls, _ensure_calls, _branch = await _drive(svc, uuid4(), unique_commits="0")

    clone = Path("/tmp/ws")
    bare_resets = [(ws, a) for ws, a in calls if a == ["reset", "--hard"]]
    checkouts_on_clone = [
        (ws, a) for ws, a in calls if a[:1] == ["checkout"] and ws == clone
    ]
    assert not bare_resets, "shared-clone `reset --hard` clobber must not run"
    assert not checkouts_on_clone, (
        "no checkout on the shared clone (worktree add replaces it)"
    )


@pytest.mark.asyncio
async def test_create_branch_calls_ensure_worktree_at_task_short_id_path() -> None:
    svc = _service()
    _stub_base(svc)
    task_id = uuid4()
    short = str(task_id)[:8]
    _, _, ensure_calls, branch = await _drive(svc, task_id, unique_commits="0")

    assert ensure_calls, "ensure_worktree must be called"
    clone_root, worktree, got_branch, base_ref = ensure_calls[0]
    assert clone_root == Path("/tmp/ws")
    assert worktree == Path("/tmp/ws") / ".worktrees" / short
    assert got_branch == branch
    # Bases off the fetched remote tip (origin/<base>), matching the old
    # `merge --ff-only origin/<base>` intent.
    assert base_ref == "origin/master"


@pytest.mark.asyncio
async def test_create_branch_pushes_branch_from_clone_root() -> None:
    svc = _service()
    _stub_base(svc)
    _, calls, _, branch = await _drive(svc, uuid4(), unique_commits="0")

    pushes = [
        a for ws, a in calls if a[:3] == ["push", "-u", "origin"] and a[3] == branch
    ]
    assert pushes, "branch must be pushed from the clone root (shared refs)"


@pytest.mark.asyncio
async def test_create_branch_returns_branch_and_base_unchanged() -> None:
    svc = _service()
    _stub_base(svc)
    out, _, _, branch = await _drive(svc, uuid4(), unique_commits="0")
    assert out == (branch, "master")


@pytest.mark.asyncio
async def test_create_branch_repoints_empty_existing_branch_on_worktree_cwd() -> None:
    # An existing branch with no commits of its own is re-pointed at the fresh
    # base — but on the WORKTREE (not the shared clone), so a sibling root's
    # tree is untouched.
    svc = _service()
    _stub_base(svc)
    task_id = uuid4()
    short = str(task_id)[:8]
    _, calls, _, _ = await _drive(svc, task_id, unique_commits="0")

    repoints = [(ws, a) for ws, a in calls if a[:2] == ["reset", "--hard"] and a[2:]]
    assert repoints, "an empty existing branch must be re-pointed to base"
    assert repoints[0][0] == Path("/tmp/ws") / ".worktrees" / short, (
        "re-point must run on the worktree, not the shared clone"
    )


@pytest.mark.asyncio
async def test_create_branch_never_repoints_branch_with_real_work() -> None:
    svc = _service()
    _stub_base(svc)
    _, calls, _, _ = await _drive(svc, uuid4(), unique_commits="3")

    repoints = [a for ws, a in calls if a[:2] == ["reset", "--hard"] and a[2:]]
    assert not repoints, "a branch carrying real work must never be re-pointed"
