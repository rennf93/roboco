"""_resolve_head_ref must not diff off a stale local ref.

Live incident (2026-07-02): the S6 cell branch advanced on ORIGIN as child
PRs squash-merged on GitHub, but the assignee clone's local ref stayed
parked pre-merge. ``diff()`` preferred the local ref, so the PR-gate
reviewer's evidence diff re-flagged work that had already landed — two
false ``pr_fail`` verdicts on a clean PR. That fix only covered the
"local strictly behind" case; a DIVERGED local ref (parked on rewritten
history after the branch was force-pushed — routine, since rebase syncs
force-push task branches) still kept local priority, so a reviewer's
evidence stayed frozen at a stale commit across every review round while
origin held the real fix.

Every caller of this method is a READER (QA/PM/documenter/PR-gate/panel
inspecting a branch they don't own), never the branch's own author mid-
write. Rule: origin wins whenever it carries anything the local ref
lacks (behind OR diverged); local keeps priority only when it strictly
contains origin (ahead on unpushed commits, or equal). Single-ref cases
are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from roboco.services.git import GitService

_BRANCH = "feature/frontend/root--cell"
_ORIGIN = f"origin/{_BRANCH}"


def _svc(*, refs: set[str], origin_only: int) -> tuple[GitService, list[list[str]]]:
    svc = GitService.__new__(GitService)
    calls: list[list[str]] = []

    async def _run_git(
        _workspace: Path, args: list[str], **_kw: Any
    ) -> SimpleNamespace:
        calls.append(args)
        if args[0] == "rev-list":
            return SimpleNamespace(returncode=0, stdout=str(origin_only))
        return SimpleNamespace(returncode=0, stdout="")

    async def _ref_exists(_workspace: Path, ref: str) -> bool:
        return ref in refs

    svc_any: Any = svc
    svc_any._run_git = _run_git
    svc_any._ref_exists = _ref_exists
    return svc, calls


@pytest.mark.asyncio
async def test_local_behind_origin_resolves_to_origin() -> None:
    svc, calls = _svc(refs={_BRANCH, _ORIGIN}, origin_only=3)
    ref = await svc._resolve_head_ref(Path("/tmp"), _BRANCH)
    assert ref == _ORIGIN
    rev_list = next(c for c in calls if c[0] == "rev-list")
    assert rev_list == ["rev-list", "--count", f"{_BRANCH}..{_ORIGIN}"]


@pytest.mark.asyncio
async def test_local_diverged_resolves_to_origin() -> None:
    """A force-pushed (rebased) branch: local carries the pre-rebase
    history under old SHAs, origin holds the rewritten tip — both sides
    have commits the other lacks by raw SHA, but a reader must still see
    origin, never the stale local rewrite (the bug: this used to keep
    local priority on any divergence, real or rewritten)."""
    svc, _calls = _svc(refs={_BRANCH, _ORIGIN}, origin_only=2)
    assert await svc._resolve_head_ref(Path("/tmp"), _BRANCH) == _ORIGIN


@pytest.mark.asyncio
async def test_local_ahead_keeps_local() -> None:
    """Committed-but-unpushed local work (origin has nothing local lacks)
    is the one case local priority still serves — the branch's own
    author, not a reader's concern."""
    svc, _calls = _svc(refs={_BRANCH, _ORIGIN}, origin_only=0)
    assert await svc._resolve_head_ref(Path("/tmp"), _BRANCH) == _BRANCH


@pytest.mark.asyncio
async def test_only_local_ref_unchanged() -> None:
    svc, calls = _svc(refs={_BRANCH}, origin_only=0)
    assert await svc._resolve_head_ref(Path("/tmp"), _BRANCH) == _BRANCH
    assert not any(c[0] == "rev-list" for c in calls)


@pytest.mark.asyncio
async def test_only_origin_ref_unchanged() -> None:
    svc, _calls = _svc(refs={_ORIGIN}, origin_only=0)
    assert await svc._resolve_head_ref(Path("/tmp"), _BRANCH) == _ORIGIN
