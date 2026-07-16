"""ReleaseExecutor: fail-closed bump → gate → commit → CI → publish (post-approval).

The executor's correctness is its ORDERING + fail-closed aborts: a red gate
aborts before any commit, a red release-commit CI aborts before publish, and a
green path publishes exactly once. Tested against a fake ops that records the
call sequence; the production git/gh ops is exercised live (CEO-gated).
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from roboco.config import settings
from roboco.services import release_executor as re
from roboco.services.release_executor import (
    ReleaseExecutor,
    ReleaseResult,
    _GitReleaseOps,
    _ReleaseContext,
    _resolve_release_ci_workflow,
)
from roboco.services.release_readiness import ReleaseReadinessReport

if TYPE_CHECKING:
    from pathlib import Path

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
        # Half-landed (publish_failed retry) detection: a prior
        # ``chore(release): {version}`` commit already on the branch. Set on the
        # instance (not via __init__ — keeps the constructor under the arg-count
        # gate) by tests that exercise the retry path.
        self._existing_sha: str | None = None
        # env-chain promotion failure message; set on the instance (same arg-
        # count-gate reason) by the promotion-failure test.
        self._promote_raises: str | None = None
        self.calls: list[str] = []
        self.bumped_plan: list[str] | None = None
        self.bumped_version: str | None = None
        self.halflanded_check = False

    async def is_already_published(self, _version: str) -> bool:
        self.calls.append("check")
        return self._already

    async def promote_env_chain(self) -> None:
        self.calls.append("promote")
        if self._promote_raises is not None:
            raise RuntimeError(self._promote_raises)

    async def release_commit_sha(self, _version: str) -> str | None:
        # Half-landed detection: a prior `chore(release): {version}` commit
        # already on the branch means a publish_failed retry must NOT re-run the
        # bump→changelog→gate→commit pipeline. Recorded via a flag (not calls)
        # so the green-path call-sequence assertion is unaffected.
        self.halflanded_check = True
        return self._existing_sha

    async def apply_version_bumps(self, plan: list[str], new_version: str) -> list[str]:
        self.calls.append("bump")
        self.bumped_plan = list(plan)
        self.bumped_version = new_version
        return list(plan)

    async def write_changelog_entry(self, _entry: str) -> None:
        self.calls.append("changelog")

    async def run_gate(self) -> tuple[bool, str]:
        self.calls.append("gate")
        return self._gate, "CI on slave@deadbeef is failure"

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
        "promote",
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
    """#88: a RuntimeError from the GitHub release POST (auth/quota/network) becomes
    a structured ``publish_failed`` result. The commit is already pushed and CI
    is green, so the release is half-landed — the CEO can retry the publish
    create`` for the same version (the executor is idempotent on the commit
    side). No 500."""
    ops = _FakeOps(publish_raises="release publish failed: HTTP 403: forbidden")
    result = await ReleaseExecutor(ops).execute(_report())
    assert result.status == "publish_failed"
    assert result.commit_sha == "deadbeef"
    assert result.release_url is None
    assert "release publish failed" in result.detail
    assert ops.calls.count("publish") == _ONE


@pytest.mark.asyncio
async def test_promotion_failure_aborts_before_bump() -> None:
    """A RuntimeError from promote_env_chain (a merge conflict in the
    head->...->prod chain) becomes a structured ``promotion_failed`` result —
    fail-closed: the bump/changelog/gate/commit/publish pipeline never runs."""
    ops = _FakeOps()
    ops._promote_raises = "env-chain promotion failed: non-fast-forward"
    result = await ReleaseExecutor(ops).execute(_report())
    assert result.status == "promotion_failed"
    assert result.commit_sha is None
    assert result.release_url is None
    assert "bump" not in ops.calls
    assert "commit" not in ops.calls
    assert "publish" not in ops.calls


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


@pytest.mark.asyncio
async def test_half_landed_retry_skips_bump_and_republishes_only() -> None:
    """#87: a publish_failed retry (commit pushed + CI green, no tag yet) must
    NOT re-run bump/changelog/gate/commit — that would re-insert the changelog
    entry above the already-present ``## [X.Y.Z]`` heading (duplicate) and land a
    second ``chore(release): X.Y.Z`` commit. The executor detects the
    half-landed state via ``release_commit_sha`` (a prior release commit already
    on the branch) and jumps straight to wait_for_ci + publish."""
    ops = _FakeOps()
    ops._existing_sha = "existingbeef"
    result = await ReleaseExecutor(ops).execute(_report())
    assert result.status == "published"
    assert result.commit_sha == "existingbeef"
    assert result.release_url is not None
    assert ops.halflanded_check is True
    assert "bump" not in ops.calls
    assert "changelog" not in ops.calls
    assert "gate" not in ops.calls
    assert "commit" not in ops.calls
    assert ops.calls == ["check", "ci", "publish"]


@pytest.mark.asyncio
async def test_wait_for_ci_scoped_to_release_commit_not_branch_latest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#318: a later commit landing on master during the ~40min wait must not
    mask the release commit's green CI. ``wait_for_ci`` scopes the GitHub query
    to the release commit_sha (``head_sha=``), so the branch-latest run (a later
    sha) can't make the gate poll forever and false-fail as ci_failed."""
    commit_sha = "release_commit_abc"
    later_sha = "later_landed_def"

    async def _fake_get_ci(_slug: str, **_kwargs: object) -> dict[str, str]:
        # Mimic GitHub's head_sha filter: a run for the release sha only when
        # asked for it (head_sha=commit_sha); the branch-latest (later commit)
        # run otherwise. The release gate MUST scope to commit_sha to see green.
        if _kwargs.get("head_sha") == commit_sha:
            return {
                "head_sha": commit_sha,
                "conclusion": "success",
                "run_url": "u",
                "run_name": "n",
                "branch": "master",
                "completed_at": "t",
            }
        return {
            "head_sha": later_sha,
            "conclusion": "success",
            "run_url": "u2",
            "run_name": "n2",
            "branch": "master",
            "completed_at": "t2",
        }

    monkeypatch.setattr(
        "roboco.services.git.get_git_service",
        lambda _session: SimpleNamespace(get_latest_ci_conclusion=_fake_get_ci),
    )
    monkeypatch.setattr(re, "_CI_MAX_POLLS", 2)

    async def _no_sleep(_secs: float) -> None:
        return None

    monkeypatch.setattr(re.asyncio, "sleep", _no_sleep)

    ctx = _ReleaseContext(
        slug="roboco-api",
        prod_branch="master",
        root=tmp_path,
        git_url="x",
        git_prefix=[],
        ci_workflow="ci.yml",
        env_chain=[],
    )
    ops = _GitReleaseOps(session=MagicMock(), ctx=ctx)
    ok = await ops.wait_for_ci(commit_sha)
    assert ok is True


@pytest.mark.asyncio
async def test_wait_for_ci_polls_through_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A completed non-success conclusion on the release sha must not abort the
    poll — a failed first attempt while a GitHub re-run is still in_progress
    (excluded from the status=completed filter) can still flip the same
    head_sha to success. Only ``conclusion == "success"`` returns True; loop
    exhaustion returns False."""
    commit_sha = "release_commit_abc"
    seq = ["failure", "failure", "success"]
    expected_polls = len(seq)
    calls = {"n": 0}

    async def _fake_get_ci(_slug: str, **kwargs: object) -> dict[str, object]:
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return {
            "head_sha": kwargs.get("head_sha", commit_sha),
            "conclusion": seq[i],
            "run_url": "u",
            "run_name": "n",
            "branch": "master",
            "completed_at": "t",
        }

    monkeypatch.setattr(
        "roboco.services.git.get_git_service",
        lambda _session: SimpleNamespace(get_latest_ci_conclusion=_fake_get_ci),
    )
    monkeypatch.setattr(re, "_CI_MAX_POLLS", 5)

    async def _no_sleep(_secs: float) -> None:
        return None

    monkeypatch.setattr(re.asyncio, "sleep", _no_sleep)

    ctx = _ReleaseContext(
        slug="roboco-api",
        prod_branch="master",
        root=tmp_path,
        git_url="x",
        git_prefix=[],
        ci_workflow="ci.yml",
        env_chain=[],
    )
    ops = _GitReleaseOps(session=MagicMock(), ctx=ctx)
    ok = await ops.wait_for_ci(commit_sha)
    assert ok is True
    assert calls["n"] == expected_polls


@pytest.mark.asyncio
async def test_wait_for_ci_exhausts_window_on_persistent_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A definitive failure that never re-runs waits the full window then
    returns False — keeps polling, never early-returns on non-success."""
    commit_sha = "release_commit_abc"
    max_polls = 3
    calls = {"n": 0}

    async def _fake_get_ci(_slug: str, **kwargs: object) -> dict[str, object]:
        calls["n"] += 1
        return {
            "head_sha": kwargs.get("head_sha", commit_sha),
            "conclusion": "failure",
            "run_url": "u",
            "run_name": "n",
            "branch": "master",
            "completed_at": "t",
        }

    monkeypatch.setattr(
        "roboco.services.git.get_git_service",
        lambda _session: SimpleNamespace(get_latest_ci_conclusion=_fake_get_ci),
    )
    monkeypatch.setattr(re, "_CI_MAX_POLLS", max_polls)

    async def _no_sleep(_secs: float) -> None:
        return None

    monkeypatch.setattr(re.asyncio, "sleep", _no_sleep)

    ctx = _ReleaseContext(
        slug="roboco-api",
        prod_branch="master",
        root=tmp_path,
        git_url="x",
        git_prefix=[],
        ci_workflow="ci.yml",
        env_chain=[],
    )
    ops = _GitReleaseOps(session=MagicMock(), ctx=ctx)
    ok = await ops.wait_for_ci(commit_sha)
    assert ok is False
    assert calls["n"] == max_polls


def test_release_ci_workflow_decoupled_from_self_heal_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#402: the release CI gate must not inherit ``self_heal_ci_workflow``'s
    empty-string tuning (documented valid for single-workflow repos), which would
    degrade the fail-closed gate to the all-workflows mode git.py itself flags as
    unreliable. The release gate always resolves a named workflow (default
    ``ci.yml``), never None."""
    # The dangerous tuning an operator might apply for self-heal on a
    # single-workflow repo — must NOT leak into the release gate.
    monkeypatch.setattr(settings, "self_heal_ci_workflow", "")
    monkeypatch.setattr(settings, "release_ci_workflow", "ci.yml")
    assert _resolve_release_ci_workflow() == "ci.yml"

    monkeypatch.setattr(settings, "release_ci_workflow", "release.yml")
    assert _resolve_release_ci_workflow() == "release.yml"

    # An empty release setting never falls through to None — always the default.
    monkeypatch.setattr(settings, "release_ci_workflow", "")
    assert _resolve_release_ci_workflow() == "ci.yml"


# --------------------------------------------------------------------------- #
# H11: the PAT must never appear in a git subprocess argv. The release clone
# and the release push carry the token via ``-c http.extraheader=Authorization:
# Basic <base64(x-access-token:TOKEN)>`` and a bare URL — never URL-embedded.
# --------------------------------------------------------------------------- #


def _basic_auth(token: str) -> str:
    return base64.b64encode(f"x-access-token:{token}".encode()).decode()


class _DoneProc:
    """A subprocess that completes immediately with a fixed rc + stdout."""

    def __init__(self, out: bytes = b"", returncode: int = 0) -> None:
        self.returncode = returncode
        self._out = out

    async def communicate(self) -> tuple[bytes, bytes]:
        return (self._out, b"")

    def kill(self) -> None:
        return None

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.asyncio
async def test_release_clone_argv_uses_extraheader_not_url_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """H11: the release-clone argv carries the PAT via ``-c http.extraheader``,
    never URL-embedded (``/proc/<pid>/cmdline`` would expose a URL token)."""
    token = "ghp_SECRETCLONE"
    git_url = "https://github.com/org/roboco.git"
    expected_basic = _basic_auth(token)
    git_prefix = ["-c", f"http.extraheader=Authorization: Basic {expected_basic}"]
    captured: list[list[str]] = []

    async def _exec(*args: str, **_kwargs: object) -> _DoneProc:
        captured.append(list(args))
        return _DoneProc()

    monkeypatch.setattr(re.asyncio, "create_subprocess_exec", _exec)
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path))

    await re._prepare_release_clone("roboco-api", git_url, git_prefix, "master")

    clone_argv = next(a for a in captured if "clone" in a)
    assert f"https://{token}@" not in " ".join(clone_argv), (
        f"raw token leaked into clone argv URL: {clone_argv}"
    )
    assert token not in clone_argv, f"raw token in clone argv: {clone_argv}"
    assert git_url in clone_argv, f"bare git_url missing from clone argv: {clone_argv}"
    assert "-c" in clone_argv
    c_idx = clone_argv.index("-c")
    assert (
        clone_argv[c_idx + 1]
        == f"http.extraheader=Authorization: Basic {expected_basic}"
    )
    assert "clone" in clone_argv[c_idx + 2 :]


@pytest.mark.asyncio
async def test_release_push_argv_uses_extraheader_not_url_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """H11: the release push argv carries the PAT via ``-c http.extraheader``
    and pushes to the bare URL — never ``https://TOKEN@host/...``."""
    token = "ghp_SECRETPUSH"
    git_url = "https://github.com/org/roboco.git"
    expected_basic = _basic_auth(token)
    git_prefix = ["-c", f"http.extraheader=Authorization: Basic {expected_basic}"]
    captured: list[list[str]] = []

    # commit_and_push issues: add -A, commit -S -m, rev-parse HEAD, push.
    responses = iter(
        [
            _DoneProc(b""),  # add -A
            _DoneProc(b""),  # commit
            _DoneProc(b"deadbeef\n"),  # rev-parse HEAD
            _DoneProc(b"ok"),  # push
        ]
    )

    async def _exec(*args: str, **_kwargs: object) -> _DoneProc:
        captured.append(list(args))
        return next(responses)

    monkeypatch.setattr(re.asyncio, "create_subprocess_exec", _exec)

    ctx = _ReleaseContext(
        slug="roboco-api",
        prod_branch="master",
        root=tmp_path,
        git_url=git_url,
        git_prefix=git_prefix,
        ci_workflow=None,
        env_chain=[],
    )
    ops = _GitReleaseOps(session=MagicMock(), ctx=ctx)
    sha = await ops.commit_and_push("0.13.0")
    assert sha == "deadbeef"

    push_argv = next(a for a in captured if "push" in a)
    assert f"https://{token}@" not in " ".join(push_argv), (
        f"raw token leaked into push argv URL: {push_argv}"
    )
    assert token not in push_argv, f"raw token in push argv: {push_argv}"
    assert git_url in push_argv, f"bare git_url missing from push argv: {push_argv}"
    assert "-c" in push_argv
    c_idx = push_argv.index("-c")
    assert (
        push_argv[c_idx + 1]
        == f"http.extraheader=Authorization: Basic {expected_basic}"
    )
    assert "push" in push_argv[c_idx + 2 :]


def test_insert_changelog_entry_empties_unreleased_body() -> None:
    existing = (
        "# Changelog\n\nintro\n\n## [Unreleased]\n\n### Added\n\n"
        "- **Curated bullet.**\n\n## [0.24.0] - 2026-07-14\n\n- old\n"
    )
    entry = "## [0.25.0] - 2026-07-15\n\n### Added\n\n- **Curated bullet.**\n"
    result = re._insert_changelog_entry(existing, entry)
    unreleased = result.split("## [Unreleased]")[1].split("## [0.25.0]")[0]
    assert "Curated bullet" not in unreleased
    assert result.count("Curated bullet") == 1
    assert result.index("## [Unreleased]") < result.index("## [0.25.0]")
    assert result.index("## [0.25.0]") < result.index("## [0.24.0]")
    assert "- old" in result


def test_insert_changelog_entry_without_unreleased_is_unchanged_behavior() -> None:
    existing = "# Changelog\n\n## [0.24.0] - 2026-07-14\n\n- old\n"
    entry = "## [0.25.0] - 2026-07-15\n\n- new\n"
    result = re._insert_changelog_entry(existing, entry)
    assert result.index("## [0.25.0]") < result.index("## [0.24.0]")
    assert "- old" in result and "- new" in result
