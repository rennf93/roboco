"""F012 — ``_GitReleaseOps.commit_and_push`` must be fail-closed on commit.

The release commit step discarded the ``git add`` / ``git commit`` return codes:
on a failed commit (gpgsign unavailable, pre-commit hook rejection, nothing to
commit after a no-op bump) the code still ran ``rev-parse HEAD`` + pushed the
pre-bump base, so ``gh release create`` would tag the *old* tree as the new
version. The fix checks both return codes and raises before any push.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from roboco.services.release_executor import _GitReleaseOps, _ReleaseContext


def _ctx() -> _ReleaseContext:
    return _ReleaseContext(
        slug="roboco",
        default_branch="master",
        root=Path("/tmp/roboco-release-f012"),
        auth_url="https://x@github.com/o/roboco",
        ci_workflow=None,
    )


class _FakeGitOps(_GitReleaseOps):
    """Overrides ``_git`` with a scripted sequence of (rc, out) tuples."""

    def __init__(self, ctx: _ReleaseContext, script: list[tuple[int, str]]) -> None:
        # Bypass the real __init__ (no session needed) — we only exercise
        # commit_and_push, which calls self._git.
        self._slug = ctx.slug
        self._default_branch = ctx.default_branch
        self._root = ctx.root
        self._auth_url = ctx.auth_url
        self._ci_workflow = ctx.ci_workflow
        self._script = list(script)
        self.calls: list[tuple[str, ...]] = []

    async def _git(self, *args: str) -> tuple[int, str]:
        self.calls.append(args)
        return self._script.pop(0)


@pytest.mark.asyncio
async def test_commit_failure_aborts_before_push() -> None:
    """A failed ``git commit`` must raise — never push the pre-bump base."""
    ops = _FakeGitOps(
        _ctx(),
        # add ok, commit FAILS (rc=1, e.g. gpgsign/pre-commit reject).
        script=[(0, ""), (1, "error: gpg failed to sign the data")],
    )
    with pytest.raises(RuntimeError, match="commit"):
        await ops.commit_and_push("0.13.0")
    # rev-parse + push must never have run.
    assert not any(c[:1] == ("rev-parse",) for c in ops.calls)
    assert not any(c[:1] == ("push",) for c in ops.calls)


@pytest.mark.asyncio
async def test_add_failure_aborts_before_commit() -> None:
    """A failed ``git add`` must raise before committing anything."""
    ops = _FakeGitOps(_ctx(), script=[(1, "fatal: pathspec did not match")])
    with pytest.raises(RuntimeError, match="add"):
        await ops.commit_and_push("0.13.0")
    assert not any(c[:1] == ("commit",) for c in ops.calls)
    assert not any(c[:1] == ("push",) for c in ops.calls)


@pytest.mark.asyncio
async def test_green_commit_then_push_returns_sha() -> None:
    """Happy path: add ok, commit ok, rev-parse sha, push ok → returns the sha."""
    ops = _FakeGitOps(
        _ctx(),
        script=[(0, ""), (0, ""), (0, "deadbeef\n"), (0, "ok")],
    )
    sha = await ops.commit_and_push("0.13.0")
    assert sha == "deadbeef"
    assert ops.calls[0][:1] == ("add",)
    assert ops.calls[1][:1] == ("commit",)
    assert ops.calls[2][:1] == ("rev-parse",)
    assert ops.calls[3][:1] == ("push",)
