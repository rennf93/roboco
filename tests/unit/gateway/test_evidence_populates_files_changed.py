"""Task #154: evidence() must populate files_changed + use full PR diff.

Bug:
    ContentActions.evidence() hard-coded ``files_changed=[]`` and called
    ``git.diff(branch_name=..., base="HEAD~1")``. Result: QA / reviewers
    inspecting a real PR saw an empty change list and only the latest
    commit's delta, even when GitHub showed a multi-commit change set.

Fix:
    Pull files via ``git.list_changed_files(branch_name=...)`` (no base
    → full diff vs parent branch). Pull diff with ``base=None`` so the
    full PR diff comes through. Both use git as the authoritative source
    instead of the legacy ``work_session.files_modified`` field, which
    the gateway ``commit()`` does not populate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.exceptions import GitTimeoutError
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _deps_for_evidence(
    task_svc: AsyncMock,
    git_svc: AsyncMock,
    workspace_svc: AsyncMock,
    evidence_repo: AsyncMock,
) -> ContentActionsDeps:
    # Findings-ledger reads (ReviewFindingsRepository.list_for_task) go
    # through session.execute — an unconfigured AsyncMock's awaited result
    # is itself an AsyncMock, so a plain sync `.scalars()` call on it leaks
    # an unawaited coroutine. Empty scalars result (no findings).
    task_svc.session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    return ContentActionsDeps(
        task=task_svc,
        git=git_svc,
        a2a=AsyncMock(),
        journal=AsyncMock(),
        workspace=workspace_svc,
        notifications=AsyncMock(),
        notification_delivery=AsyncMock(),
        evidence_repo=evidence_repo,
    )


def _task_with_pr(task_id: object, *, commits: list[str]) -> MagicMock:
    return MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=None,
        branch_name="feature/backend/abc12345--def67890",
        work_session_id=uuid4(),
        commits=commits,
        pr_number=20,
        pr_url="https://github.com/org/repo/pull/20",
        dev_notes="see PR description",
        acceptance_criteria_status=[],
    )


@pytest.mark.asyncio
async def test_evidence_populates_files_changed_from_git() -> None:
    """The smoke-9 regression: PR #20 has README change on GitHub but
    evidence() reports files_changed=[]. The fix queries git directly."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = _task_with_pr(task_id, commits=["abc", "def"])
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = (
        "diff --git a/README.md b/README.md\n+added line\n",
        ["README.md", "docs/guide.md"],
    )
    workspace_svc = AsyncMock()
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []

    ca = ContentActions(
        _deps_for_evidence(task_svc, git_svc, workspace_svc, evidence_repo)
    )
    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    body = env.as_dict()

    assert body["error"] is None
    assert body["evidence"]["files_changed"] == ["README.md", "docs/guide.md"]
    assert "diff --git" in body["evidence"]["pr_diff_summary"]
    git_svc.diff_and_files.assert_awaited_once()


@pytest.mark.asyncio
async def test_evidence_uses_full_pr_diff_not_head_minus_one() -> None:
    """git.diff_and_files must be called with base=None (full PR diff vs
    parent), not base='HEAD~1' (only the last commit)."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    # Multi-commit branch — the pre-fix code passed base='HEAD~1' when
    # task.commits was non-empty, masking earlier commits' changes.
    task_svc.get.return_value = _task_with_pr(task_id, commits=["sha1", "sha2", "sha3"])
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("full diff", [])
    workspace_svc = AsyncMock()
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []

    ca = ContentActions(
        _deps_for_evidence(task_svc, git_svc, workspace_svc, evidence_repo)
    )
    await ca.evidence(agent_id=agent_id, task_id=task_id)

    git_svc.diff_and_files.assert_awaited_once()
    call_kwargs = git_svc.diff_and_files.await_args.kwargs
    # Pre-fix bug: kwargs['base'] would be 'HEAD~1' for any multi-commit
    # branch. Post-fix: base is omitted (or explicitly None).
    base = call_kwargs.get("base")
    assert base in (None, ""), (
        f"git.diff_and_files must use full-PR diff (base=None), got base={base!r}"
    )
    assert call_kwargs.get("branch_name") == "feature/backend/abc12345--def67890"


@pytest.mark.asyncio
async def test_evidence_populates_journal_highlights() -> None:
    """evidence() must return journal_highlights so QA gets the dev's
    decision/reflection context — same as qa.py's claim_review evidence."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = _task_with_pr(task_id, commits=["abc"])
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("", [])
    workspace_svc = AsyncMock()
    evidence_repo = AsyncMock()
    highlights = [
        {"scope": "decision", "title": "Use README format X", "content": "..."},
        {"scope": "reflect", "title": "Lesson learned", "content": "..."},
    ]
    evidence_repo.journal_highlights_for_task.return_value = highlights

    ca = ContentActions(
        _deps_for_evidence(task_svc, git_svc, workspace_svc, evidence_repo)
    )
    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    body = env.as_dict()
    assert body["evidence"]["journal_highlights"] == highlights
    evidence_repo.journal_highlights_for_task.assert_awaited_once_with(
        task_id, include_ancestors=True
    )


@pytest.mark.asyncio
async def test_evidence_commits_session_before_git_work() -> None:
    """2026-07-29 pool exhaustion: evidence() must release its DB transaction
    (commit) BEFORE the fetch/diff git work — those can run for minutes on a
    cold workspace, and an open transaction pins a pool connection for the
    whole duration."""
    order: list[str] = []
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = _task_with_pr(task_id, commits=["abc"])
    task_svc.session.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("", [])
    workspace_svc = AsyncMock()
    workspace_svc.fetch_branch_for_inspection = AsyncMock(
        side_effect=lambda **_kw: order.append("fetch")
    )
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []

    ca = ContentActions(
        _deps_for_evidence(task_svc, git_svc, workspace_svc, evidence_repo)
    )
    env = await ca.evidence(agent_id=agent_id, task_id=task_id)

    assert env.as_dict()["error"] is None
    assert order == ["commit", "fetch"], (
        f"session must be committed before git work, got order={order}"
    )


@pytest.mark.asyncio
async def test_evidence_no_branch_skips_git_calls() -> None:
    """A task without a branch_name has no PR yet — skip git entirely,
    still return a valid envelope with empty files_changed."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    no_branch = MagicMock(
        id=task_id,
        status="claimed",
        assigned_to=agent_id,
        branch_name=None,
        work_session_id=None,
        commits=[],
        pr_number=None,
        pr_url=None,
        dev_notes=None,
        acceptance_criteria_status=[],
    )
    task_svc.get.return_value = no_branch
    git_svc = AsyncMock()
    workspace_svc = AsyncMock()
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []

    ca = ContentActions(
        _deps_for_evidence(task_svc, git_svc, workspace_svc, evidence_repo)
    )
    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    body = env.as_dict()
    assert body["error"] is None
    assert body["evidence"]["files_changed"] == []
    assert body["evidence"]["pr_diff_summary"] == ""
    git_svc.diff_and_files.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bounded advisory-evidence legs: evidence() must not hang on a slow branch
# fetch / diff / list_changed_files leg — it degrades and records a note in
# evidence_gaps instead (same run_bounded_leg treatment as claim_review /
# claim_doc_task / claim_gate_review). Every timeout-shaped test is
# parametrized over both real timeout shapes: asyncio's own
# cancellation-converted TimeoutError, and GitTimeoutError (_run_git's own
# internal subprocess bound — a GitError/RobocoError subclass, NOT a
# TimeoutError subclass, and the most common real-world single-hung-git-call
# shape since it defaults to a SHORTER window than a leg's own budget).
# ---------------------------------------------------------------------------

_TIMEOUT_EXCEPTIONS = (
    TimeoutError("hung"),
    GitTimeoutError("git diff", 30),
)
_TIMEOUT_IDS = ("asyncio_timeout", "git_timeout")


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _TIMEOUT_EXCEPTIONS, ids=_TIMEOUT_IDS)
async def test_evidence_diff_timeout_degrades_with_gap(exc: Exception) -> None:
    """A hung git.diff_and_files must not hang evidence(): the combined
    diff+files leg degrades to an empty diff and an empty files_changed
    together (one leg, one resolution — a timeout on either subprocess
    loses both), and records the gap."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = _task_with_pr(task_id, commits=["abc"])
    git_svc = AsyncMock()
    git_svc.diff_and_files.side_effect = exc
    workspace_svc = AsyncMock()
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []

    ca = ContentActions(
        _deps_for_evidence(task_svc, git_svc, workspace_svc, evidence_repo)
    )
    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff_summary"] == ""
    assert ev["files_changed"] == []
    assert "evidence_gaps" in ev
    # A combined-leg timeout kills diff AND files_changed together — the
    # gap note names both losses, not just "pr diff", so a reader can tell
    # files_changed is empty because of a timeout, not genuinely empty.
    assert any("pr diff + files_changed unavailable" in g for g in ev["evidence_gaps"])


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _TIMEOUT_EXCEPTIONS, ids=_TIMEOUT_IDS)
async def test_evidence_branch_fetch_timeout_degrades_with_gap(exc: Exception) -> None:
    """A hung workspace branch-fetch must not hang evidence() either — the
    subsequent diff_and_files leg still runs (against whatever the
    workspace already has) and the gap is recorded."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = _task_with_pr(task_id, commits=["abc"])
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("diff content", ["README.md"])
    workspace_svc = AsyncMock()
    workspace_svc.fetch_branch_for_inspection.side_effect = exc
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []

    ca = ContentActions(
        _deps_for_evidence(task_svc, git_svc, workspace_svc, evidence_repo)
    )
    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff_summary"] == "diff content"
    assert ev["files_changed"] == ["README.md"]
    assert "evidence_gaps" in ev
    assert any("branch fetch unavailable" in g for g in ev["evidence_gaps"])


@pytest.mark.asyncio
async def test_evidence_branch_fetch_passes_subprocess_timeout_from_budget() -> None:
    """The branch-fetch leg passes its own remaining LegBudget share down as
    fetch_branch_for_inspection's subprocess_timeout, so a hung fetch
    subprocess self-terminates near the leg's own budget instead of
    occupying a thread on the shared default executor for up to
    workspace_clone_timeout (300s) after evidence() already gave up on it."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = _task_with_pr(task_id, commits=["abc"])
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("diff content", ["README.md"])
    workspace_svc = AsyncMock()
    workspace_svc.fetch_branch_for_inspection.return_value = None
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []

    ca = ContentActions(
        _deps_for_evidence(task_svc, git_svc, workspace_svc, evidence_repo)
    )
    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    assert env.as_dict()["error"] is None

    workspace_svc.fetch_branch_for_inspection.assert_awaited_once()
    call_kwargs = workspace_svc.fetch_branch_for_inspection.await_args.kwargs
    assert (
        call_kwargs["subprocess_timeout"] <= settings.evidence_assembly_timeout_seconds
    )
    assert call_kwargs["subprocess_timeout"] > 0


@pytest.mark.asyncio
async def test_evidence_normal_path_has_no_evidence_gaps() -> None:
    """Byte-for-byte unchanged normal path: no evidence_gaps key at all when
    nothing times out."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = _task_with_pr(task_id, commits=["abc"])
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("diff content", ["README.md"])
    workspace_svc = AsyncMock()
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []

    ca = ContentActions(
        _deps_for_evidence(task_svc, git_svc, workspace_svc, evidence_repo)
    )
    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff_summary"] == "diff content"
    assert ev["files_changed"] == ["README.md"]
    assert "evidence_gaps" not in ev
