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
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _deps_for_evidence(
    task_svc: AsyncMock,
    git_svc: AsyncMock,
    workspace_svc: AsyncMock,
    evidence_repo: AsyncMock,
) -> ContentActionsDeps:
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
    git_svc.diff.return_value = "diff --git a/README.md b/README.md\n+added line\n"
    git_svc.list_changed_files.return_value = ["README.md", "docs/guide.md"]
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
    git_svc.list_changed_files.assert_awaited_once()


@pytest.mark.asyncio
async def test_evidence_uses_full_pr_diff_not_head_minus_one() -> None:
    """git.diff must be called with base=None (full PR diff vs parent),
    not base='HEAD~1' (only the last commit)."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    # Multi-commit branch — the pre-fix code passed base='HEAD~1' when
    # task.commits was non-empty, masking earlier commits' changes.
    task_svc.get.return_value = _task_with_pr(task_id, commits=["sha1", "sha2", "sha3"])
    git_svc = AsyncMock()
    git_svc.diff.return_value = "full diff"
    git_svc.list_changed_files.return_value = []
    workspace_svc = AsyncMock()
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []

    ca = ContentActions(
        _deps_for_evidence(task_svc, git_svc, workspace_svc, evidence_repo)
    )
    await ca.evidence(agent_id=agent_id, task_id=task_id)

    git_svc.diff.assert_awaited_once()
    call_kwargs = git_svc.diff.await_args.kwargs
    # Pre-fix bug: kwargs['base'] would be 'HEAD~1' for any multi-commit
    # branch. Post-fix: base is omitted (or explicitly None).
    base = call_kwargs.get("base")
    assert base in (None, ""), (
        f"git.diff must use full-PR diff (base=None), got base={base!r}"
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
    git_svc.diff.return_value = ""
    git_svc.list_changed_files.return_value = []
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
    git_svc.diff.assert_not_awaited()
    git_svc.list_changed_files.assert_not_awaited()
