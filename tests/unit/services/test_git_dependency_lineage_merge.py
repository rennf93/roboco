"""``GitService.merge_dependency_lineage`` — the cross-subtree/cross-cell
dependency-lineage gap.

The claim-time dependency gate (``TaskService._claim_blocked_by_dependencies``)
only enforces TIMING — a dependency must be terminal before the dependent task
can claim — it never checks whether the dependency's MERGED content is
reachable from the dependent's freshly cut branch. A cross-subtree/cross-cell
edge (e.g. a UX cell task merged into the UX cell branch, depended on by a
sibling frontend cell task cut from the frontend cell branch) can complete on
a branch the dependent never descends from.

This backfills it at branch-cut time: fetch the dependency's merge-target
branch, no-op if it is already an ancestor of the fresh branch (the common,
transitively-safe case), otherwise merge it in. On a real conflict the merge
is aborted and the branch is left exactly at its cut point — a content
assist, never a gate, so it always returns a status rather than raising.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.git import GitService

_WORKSPACE = Path("/tmp/fake-ws")
_TASK_ID = uuid4()
_WORKTREE = _WORKSPACE / ".worktrees" / str(_TASK_ID)[:8]
_BRANCH = "feature/frontend/root--fe-cell"
_SOURCE = "feature/ux_ui/root--ux-cell"
_ORIGIN_SOURCE = f"origin/{_SOURCE}"


def _git_service() -> GitService:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    return svc


def _result(returncode: int = 0, stdout: str = "") -> Any:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    return r


def _drive(
    svc: GitService, responses: dict[tuple[str, ...], Any]
) -> list[tuple[Path, list[str]]]:
    """Stub _run_git to answer by args-prefix; _ensure_worktree_for_commit +
    _token_for_project are no-ops so only merge mechanics are under test."""
    calls: list[tuple[Path, list[str]]] = []

    async def _run(workspace: Path, args: list[str], **_kw: object) -> Any:
        calls.append((Path(workspace), list(args)))
        for prefix, resp in responses.items():
            if tuple(args[: len(prefix)]) == prefix:
                return resp
        return _result()

    object.__setattr__(svc, "_run_git", _run)
    object.__setattr__(svc, "_token_for_project", AsyncMock(return_value="tok"))
    object.__setattr__(svc, "_ensure_worktree_for_commit", AsyncMock())
    return calls


@pytest.mark.asyncio
async def test_already_ancestor_is_a_no_op() -> None:
    """source_branch already reachable from branch_name: no merge, no push —
    the common, transitively-safe case (same-parent siblings, root waves)."""
    svc = _git_service()
    calls = _drive(
        svc,
        {
            ("rev-parse", "--verify", "--quiet"): _result(returncode=0),
            ("merge-base", "--is-ancestor"): _result(returncode=0),
        },
    )

    result = await svc.merge_dependency_lineage(
        _WORKSPACE, _TASK_ID, _BRANCH, _SOURCE, project_slug="roboco-api"
    )

    assert result == {"status": "already_ancestor"}
    assert not [c for c in calls if c[1][:1] == ["merge"]], (
        "an already-ancestor source must never be merged"
    )
    assert not [c for c in calls if c[1][:1] == ["push"]]


@pytest.mark.asyncio
async def test_missing_ref_short_circuits_before_touching_worktree() -> None:
    """source_branch not on origin: no worktree touched, no merge attempted."""
    svc = _git_service()
    calls = _drive(svc, {("rev-parse", "--verify", "--quiet"): _result(returncode=1)})

    result = await svc.merge_dependency_lineage(
        _WORKSPACE, _TASK_ID, _BRANCH, _SOURCE, project_slug="roboco-api"
    )

    assert result == {"status": "missing_ref"}
    ensure = object.__getattribute__(svc, "_ensure_worktree_for_commit")
    ensure.assert_not_awaited()
    assert not [c for c in calls if c[1][:1] == ["merge-base"]]


@pytest.mark.asyncio
async def test_outside_lineage_merges_and_pushes_from_the_worktree() -> None:
    """source_branch NOT an ancestor: merged into the worktree and pushed —
    the actual gap fix (cross-cell dependency content backfilled)."""
    svc = _git_service()
    calls = _drive(
        svc,
        {
            ("rev-parse", "--verify", "--quiet"): _result(returncode=0),
            ("merge-base", "--is-ancestor"): _result(returncode=1),
            ("merge", "--no-edit"): _result(returncode=0),
            ("push", "origin"): _result(returncode=0),
        },
    )

    result = await svc.merge_dependency_lineage(
        _WORKSPACE, _TASK_ID, _BRANCH, _SOURCE, project_slug="roboco-api"
    )

    assert result == {"status": "merged"}
    merge_calls = [c for c in calls if c[1][:1] == ["merge"]]
    push_calls = [c for c in calls if c[1][:1] == ["push"]]
    assert merge_calls and all(ws == _WORKTREE for ws, _ in merge_calls), (
        "the merge must run in the task's worktree, not the shared clone"
    )
    assert push_calls and push_calls[0][1] == ["push", "origin", _BRANCH]


@pytest.mark.asyncio
async def test_merge_conflict_aborts_and_reports_files() -> None:
    """A real conflict: abort, leave the branch at its cut point, never push."""
    svc = _git_service()
    calls = _drive(
        svc,
        {
            ("rev-parse", "--verify", "--quiet"): _result(returncode=0),
            ("merge-base", "--is-ancestor"): _result(returncode=1),
            ("merge", "--no-edit"): _result(returncode=1),
            ("diff", "--name-only", "--diff-filter=U"): _result(
                stdout="src/a.py\nsrc/b.py\n"
            ),
            ("merge", "--abort"): _result(returncode=0),
        },
    )

    result = await svc.merge_dependency_lineage(
        _WORKSPACE, _TASK_ID, _BRANCH, _SOURCE, project_slug="roboco-api"
    )

    assert result == {"status": "conflict", "files": ["src/a.py", "src/b.py"]}
    assert not [c for c in calls if c[1][:1] == ["push"]], (
        "a conflicted merge must never push"
    )
    abort_calls = [c for c in calls if c[1] == ["merge", "--abort"]]
    assert abort_calls, "a failed merge must be aborted, restoring the branch"


@pytest.mark.asyncio
async def test_merge_succeeds_but_push_fails_is_reported_distinctly() -> None:
    """A clean local merge that can't reach origin is not silently "merged"."""
    svc = _git_service()
    _drive(
        svc,
        {
            ("rev-parse", "--verify", "--quiet"): _result(returncode=0),
            ("merge-base", "--is-ancestor"): _result(returncode=1),
            ("merge", "--no-edit"): _result(returncode=0),
            ("push", "origin"): _result(returncode=1),
        },
    )

    result = await svc.merge_dependency_lineage(
        _WORKSPACE, _TASK_ID, _BRANCH, _SOURCE, project_slug="roboco-api"
    )

    assert result == {"status": "merged_push_failed"}
