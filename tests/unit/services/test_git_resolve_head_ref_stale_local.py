"""_resolve_head_ref must not diff off a stale local ref.

Live incident (2026-07-02): the S6 cell branch advanced on ORIGIN as child
PRs squash-merged on GitHub, but the assignee clone's local ref stayed
parked pre-merge. ``diff()`` preferred the local ref, so the PR-gate
reviewer's evidence diff re-flagged work that had already landed — two
false ``pr_fail`` verdicts on a clean PR.

Rule: when both refs exist and the local ref is STRICTLY BEHIND origin,
use ``origin/<branch>``; a local ref that is ahead (unpushed commits) or
diverged keeps priority, and single-ref cases are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from roboco.services.git import GitService

_BRANCH = "feature/frontend/root--cell"
_ORIGIN = f"origin/{_BRANCH}"


def _svc(*, refs: set[str], ancestor_rc: int) -> tuple[GitService, list[list[str]]]:
    svc = GitService.__new__(GitService)
    calls: list[list[str]] = []

    async def _run_git(
        _workspace: Path, args: list[str], **_kw: Any
    ) -> SimpleNamespace:
        calls.append(args)
        if args[0] == "merge-base":
            return SimpleNamespace(returncode=ancestor_rc, stdout="")
        return SimpleNamespace(returncode=0, stdout="")

    async def _ref_exists(_workspace: Path, ref: str) -> bool:
        return ref in refs

    svc_any: Any = svc
    svc_any._run_git = _run_git
    svc_any._ref_exists = _ref_exists
    return svc, calls


@pytest.mark.asyncio
async def test_local_behind_origin_resolves_to_origin() -> None:
    svc, calls = _svc(refs={_BRANCH, _ORIGIN}, ancestor_rc=0)
    ref = await svc._resolve_head_ref(Path("/tmp"), _BRANCH)
    assert ref == _ORIGIN
    ancestor = next(c for c in calls if c[0] == "merge-base")
    assert ancestor == ["merge-base", "--is-ancestor", _BRANCH, _ORIGIN]


@pytest.mark.asyncio
async def test_local_ahead_or_diverged_keeps_local() -> None:
    svc, _calls = _svc(refs={_BRANCH, _ORIGIN}, ancestor_rc=1)
    assert await svc._resolve_head_ref(Path("/tmp"), _BRANCH) == _BRANCH


@pytest.mark.asyncio
async def test_only_local_ref_unchanged() -> None:
    svc, calls = _svc(refs={_BRANCH}, ancestor_rc=1)
    assert await svc._resolve_head_ref(Path("/tmp"), _BRANCH) == _BRANCH
    assert not any(c[0] == "merge-base" for c in calls)


@pytest.mark.asyncio
async def test_only_origin_ref_unchanged() -> None:
    svc, _calls = _svc(refs={_ORIGIN}, ancestor_rc=1)
    assert await svc._resolve_head_ref(Path("/tmp"), _BRANCH) == _ORIGIN
