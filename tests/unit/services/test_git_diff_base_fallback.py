"""Task #161: GitService diff base falls back to default branch.

A leaf dev branch's parent (per parent_branch_for) is the cell-PM
branch feature/{team}/{root}--{cellpm}, which is NEVER pushed — only
devs push their own leaf branch. Diffing against a non-existent
origin/<parent> returns an empty diff, so QA / docs saw nothing.
_resolve_diff_base must fall back to the repo default branch when the
parent ref is absent on origin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from roboco.services.git import GitService


def _git_service() -> GitService:
    return GitService.__new__(GitService)


@pytest.mark.asyncio
async def test_resolve_diff_base_uses_parent_when_pushed() -> None:
    """When origin/<parent> exists, use it (normal case)."""
    svc = _git_service()
    svc._run_git = AsyncMock()  # type: ignore[method-assign]
    svc._ref_exists = AsyncMock(return_value=True)  # type: ignore[method-assign]
    ws = Path("/tmp/ws")

    base = await svc._resolve_diff_base(
        ws, "feature/backend/root1234--cellpm56--dev78901"
    )
    # parent_branch_for strips the last --segment.
    assert base == "origin/feature/backend/root1234--cellpm56"


@pytest.mark.asyncio
async def test_resolve_diff_base_falls_back_when_parent_absent() -> None:
    """When origin/<parent> does NOT exist (cell-PM branch never pushed),
    fall back to the repo default branch via origin/HEAD."""
    svc = _git_service()
    svc._run_git = AsyncMock()  # type: ignore[method-assign]
    # parent ref absent → _ref_exists False for the parent check.
    svc._ref_exists = AsyncMock(return_value=False)  # type: ignore[method-assign]
    svc._default_branch_ref = AsyncMock(  # type: ignore[method-assign]
        return_value="origin/master"
    )
    ws = Path("/tmp/ws")

    base = await svc._resolve_diff_base(
        ws, "feature/backend/root1234--cellpm56--dev78901"
    )
    assert base == "origin/master"
    svc._default_branch_ref.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_branch_ref_prefers_origin_head() -> None:
    """origin/HEAD symbolic-ref is the canonical default-branch pointer."""
    svc = _git_service()

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        if args[:2] == ["symbolic-ref", "--quiet"]:
            return type(
                "R", (), {"returncode": 0, "stdout": "refs/remotes/origin/main\n"}
            )()
        return type("R", (), {"returncode": 1, "stdout": ""})()

    svc._run_git = fake_run  # type: ignore[method-assign]
    ref = await svc._default_branch_ref(Path("/tmp/ws"))
    assert ref == "origin/main"


@pytest.mark.asyncio
async def test_default_branch_ref_fallback_when_no_head() -> None:
    """No origin/HEAD → probe origin/master then origin/main; final
    hard fallback is origin/master so the git invocation stays valid."""
    svc = _git_service()

    async def fake_run(_ws: Any, _args: list[str], **_kw: Any) -> Any:
        # symbolic-ref fails; fetches succeed but ref never verifies.
        return type("R", (), {"returncode": 1, "stdout": ""})()

    svc._run_git = fake_run  # type: ignore[method-assign]
    svc._ref_exists = AsyncMock(return_value=False)  # type: ignore[method-assign]
    ref = await svc._default_branch_ref(Path("/tmp/ws"))
    assert ref == "origin/master"


# ---------------------------------------------------------------------------
# Task #161 (facet): the diff HEAD side must resolve in the inspecting
# clone. The local <branch> ref only exists in the dev's own clone (the
# clone that ran `git checkout -b` at claim). QA / doc / PM diff from
# their OWN clones, which only have origin/<branch> after a fetch. Diffing
# against the bare local name there yields an empty diff (smoke-14: QA saw
# no changes on a real PR). _resolve_head_ref + diff()/list_changed_files
# must prefer the local branch, then origin/<branch>.
# ---------------------------------------------------------------------------

_BR = "feature/backend/root1234--cellpm56--dev78901"


@pytest.mark.asyncio
async def test_resolve_head_ref_prefers_local_branch_in_dev_clone() -> None:
    """Dev's own clone has the local branch — use it unchanged."""
    svc = _git_service()
    svc._run_git = AsyncMock()  # type: ignore[method-assign]
    svc._ref_exists = AsyncMock(return_value=True)  # type: ignore[method-assign]

    head = await svc._resolve_head_ref(Path("/tmp/ws"), _BR)
    assert head == _BR


@pytest.mark.asyncio
async def test_resolve_head_ref_falls_back_to_origin_in_foreign_clone() -> None:
    """QA/doc/PM clone has no local branch but origin/<branch> exists
    (open_pr pushed it) — diff must target origin/<branch>."""
    svc = _git_service()
    svc._run_git = AsyncMock()  # type: ignore[method-assign]

    async def ref_exists(_ws: Any, ref: str) -> bool:
        # local branch absent; only the remote-tracking ref resolves.
        return ref == f"origin/{_BR}"

    svc._ref_exists = ref_exists  # type: ignore[method-assign]
    head = await svc._resolve_head_ref(Path("/tmp/ws"), _BR)
    assert head == f"origin/{_BR}"


@pytest.mark.asyncio
async def test_resolve_head_ref_fetches_branch_before_resolving() -> None:
    """The branch is fetched into the workspace so origin/<branch> is
    available even on a clone that never saw it."""
    svc = _git_service()
    calls: list[list[str]] = []

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        calls.append(args)
        return type("R", (), {"returncode": 0, "stdout": ""})()

    svc._run_git = fake_run  # type: ignore[method-assign]
    svc._ref_exists = AsyncMock(return_value=True)  # type: ignore[method-assign]
    await svc._resolve_head_ref(Path("/tmp/ws"), _BR)
    assert ["fetch", "origin", _BR] in calls


@pytest.mark.asyncio
async def test_diff_targets_origin_head_in_foreign_clone() -> None:
    """Regression for smoke-14: diff() from QA's clone must compare
    base...origin/<branch>, not base...<bare-local-branch> (which is
    unresolvable there and silently produced an empty diff)."""
    svc = _git_service()
    svc._workspace_for_branch = AsyncMock(  # type: ignore[method-assign]
        return_value=Path("/tmp/qa-ws")
    )
    svc._resolve_diff_base = AsyncMock(  # type: ignore[method-assign]
        return_value="origin/master"
    )
    svc._resolve_head_ref = AsyncMock(  # type: ignore[method-assign]
        return_value=f"origin/{_BR}"
    )
    captured: list[list[str]] = []

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        captured.append(args)
        return type("R", (), {"returncode": 0, "stdout": "diff body"})()

    svc._run_git = fake_run  # type: ignore[method-assign]

    out = await svc.diff(branch_name=_BR)
    assert out == "diff body"
    assert captured == [["diff", f"origin/master...origin/{_BR}"]]


@pytest.mark.asyncio
async def test_list_changed_files_targets_origin_head_in_foreign_clone() -> None:
    """Same fix on the files_changed path (#154 evidence)."""
    svc = _git_service()
    svc._workspace_for_branch = AsyncMock(  # type: ignore[method-assign]
        return_value=Path("/tmp/qa-ws")
    )
    svc._resolve_diff_base = AsyncMock(  # type: ignore[method-assign]
        return_value="origin/master"
    )
    svc._resolve_head_ref = AsyncMock(  # type: ignore[method-assign]
        return_value=f"origin/{_BR}"
    )
    captured: list[list[str]] = []

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        captured.append(args)
        return type("R", (), {"returncode": 0, "stdout": "README.md\nsrc/app.py\n"})()

    svc._run_git = fake_run  # type: ignore[method-assign]

    files = await svc.list_changed_files(branch_name=_BR)
    assert files == ["README.md", "src/app.py"]
    assert captured == [["diff", "--name-only", f"origin/master...origin/{_BR}"]]


@pytest.mark.asyncio
async def test_diff_honours_explicit_base_with_resolved_head() -> None:
    """The incremental dev path (base=HEAD~1) still works: explicit base
    is preserved, head still goes through _resolve_head_ref."""
    svc = _git_service()
    svc._workspace_for_branch = AsyncMock(  # type: ignore[method-assign]
        return_value=Path("/tmp/dev-ws")
    )
    svc._resolve_diff_base = AsyncMock(  # type: ignore[method-assign]
        return_value="SHOULD_NOT_BE_USED"
    )
    svc._resolve_head_ref = AsyncMock(  # type: ignore[method-assign]
        return_value=_BR
    )
    captured: list[list[str]] = []

    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        captured.append(args)
        return type("R", (), {"returncode": 0, "stdout": ""})()

    svc._run_git = fake_run  # type: ignore[method-assign]
    await svc.diff(branch_name=_BR, base="HEAD~1")
    assert captured == [["diff", f"HEAD~1...{_BR}"]]
    svc._resolve_diff_base.assert_not_awaited()
