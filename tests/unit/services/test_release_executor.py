"""ReleaseExecutor: fail-closed bump → gate → commit → CI → publish (post-approval).

The executor's correctness is its ORDERING + fail-closed aborts: a red gate
aborts before any commit, a red release-commit CI aborts before publish, and a
green path publishes exactly once. Tested against a fake ops that records the
call sequence; the production git/gh ops is exercised live (CEO-gated).
"""

from __future__ import annotations

import pytest
from roboco.services.release_executor import ReleaseExecutor, ReleaseResult
from roboco.services.release_readiness import ReleaseReadinessReport

_PLAN = ["pyproject.toml", "roboco/__init__.py", "CHANGELOG.md"]
_VERSION = "0.13.0"
_ONE = 1


def _report() -> ReleaseReadinessReport:
    return ReleaseReadinessReport(
        proposed_version=_VERSION,
        bump_kind="minor",
        change_summary=["feat: a thing"],
        drafted_changelog=(
            f"## [{_VERSION}] - 2026-06-25\n\n### Added\n- a thing (#1)\n"
        ),
        version_bump_plan=list(_PLAN),
        gaps=[],
        migration_notes=[],
        gate_state="green",
    )


class _FakeOps:
    """Records the call sequence; flags drive gate/CI/already-published outcomes."""

    def __init__(
        self,
        *,
        already: bool = False,
        gate: bool = True,
        ci: bool = True,
        commit_raises: str | None = None,
        publish_raises: str | None = None,
    ):
        self._already = already
        self._gate = gate
        self._ci = ci
        self._commit_raises = commit_raises
        self._publish_raises = publish_raises
        self.calls: list[str] = []
        self.bumped_plan: list[str] | None = None
        self.bumped_version: str | None = None

    async def is_already_published(self, _version: str) -> bool:
        self.calls.append("check")
        return self._already

    async def apply_version_bumps(self, plan: list[str], new_version: str) -> list[str]:
        self.calls.append("bump")
        self.bumped_plan = list(plan)
        self.bumped_version = new_version
        return list(plan)

    async def write_changelog_entry(self, _entry: str) -> None:
        self.calls.append("changelog")

    async def run_gate(self) -> bool:
        self.calls.append("gate")
        return self._gate

    async def commit_and_push(self, _version: str) -> str:
        self.calls.append("commit")
        if self._commit_raises is not None:
            raise RuntimeError(self._commit_raises)
        return "deadbeef"

    async def wait_for_ci(self, _commit_sha: str) -> bool:
        self.calls.append("ci")
        return self._ci

    async def publish_release(self, version: str, _notes: str) -> str:
        self.calls.append("publish")
        if self._publish_raises is not None:
            raise RuntimeError(self._publish_raises)
        return f"https://github.com/x/roboco/releases/tag/v{version}"


@pytest.mark.asyncio
async def test_green_path_publishes_once() -> None:
    ops = _FakeOps()
    result = await ReleaseExecutor(ops).execute(_report())
    assert result.status == "published"
    assert result.release_url is not None
    assert result.commit_sha == "deadbeef"
    assert ops.calls.count("publish") == _ONE
    assert ops.calls == [
        "check",
        "bump",
        "changelog",
        "gate",
        "commit",
        "ci",
        "publish",
    ]


@pytest.mark.asyncio
async def test_bump_targets_the_canonical_set() -> None:
    ops = _FakeOps()
    result = await ReleaseExecutor(ops).execute(_report())
    assert ops.bumped_plan == _PLAN
    assert ops.bumped_version == _VERSION
    assert result.files_changed == _PLAN


@pytest.mark.asyncio
async def test_red_gate_aborts_before_commit() -> None:
    ops = _FakeOps(gate=False)
    result = await ReleaseExecutor(ops).execute(_report())
    assert result.status == "gate_failed"
    assert "commit" not in ops.calls
    assert "publish" not in ops.calls


@pytest.mark.asyncio
async def test_red_ci_aborts_before_publish() -> None:
    ops = _FakeOps(ci=False)
    result = await ReleaseExecutor(ops).execute(_report())
    assert result.status == "ci_failed"
    assert "commit" in ops.calls
    assert "publish" not in ops.calls


@pytest.mark.asyncio
async def test_already_published_is_a_noop() -> None:
    ops = _FakeOps(already=True)
    result = await ReleaseExecutor(ops).execute(_report())
    assert result.status == "already_published"
    assert "bump" not in ops.calls
    assert "commit" not in ops.calls
    assert "publish" not in ops.calls


@pytest.mark.asyncio
async def test_commit_push_failure_returns_structured_commit_failed() -> None:
    """#88: a RuntimeError from commit_and_push (gpgsign/pre-commit/non-ff
    push) becomes a structured ``commit_failed`` result — not a 500 bubbling
    out of ``approve``. Fail-closed: publish never runs."""
    ops = _FakeOps(commit_raises="release push failed: non-fast-forward")
    result = await ReleaseExecutor(ops).execute(_report())
    assert result.status == "commit_failed"
    assert result.commit_sha is None
    assert result.release_url is None
    assert "commit_failed" in result.detail or "push failed" in result.detail
    assert "publish" not in ops.calls
    assert "ci" not in ops.calls


@pytest.mark.asyncio
async def test_publish_failure_returns_structured_publish_failed() -> None:
    """#88: a RuntimeError from ``gh release create`` (auth/quota/network) becomes
    a structured ``publish_failed`` result. The commit is already pushed and CI
    is green, so the release is half-landed — the CEO can retry ``gh release
    create`` for the same version (the executor is idempotent on the commit
    side). No 500."""
    ops = _FakeOps(publish_raises="gh release create failed: forbidden")
    result = await ReleaseExecutor(ops).execute(_report())
    assert result.status == "publish_failed"
    assert result.commit_sha == "deadbeef"
    assert result.release_url is None
    assert "gh release create failed" in result.detail
    assert ops.calls.count("publish") == _ONE


def test_release_result_carries_outcome_fields() -> None:
    result = ReleaseResult(
        status="published",
        version=_VERSION,
        files_changed=list(_PLAN),
        commit_sha="abc",
        release_url="https://example/releases/v0.13.0",
        detail="ok",
    )
    assert result.version == _VERSION
    assert result.files_changed == _PLAN
    assert result.release_url is not None
