"""Task #162: claim_doc_task checks out the task branch + verb hints.

Smoke-11: be-doc claimed an awaiting_documentation task but its clone
stayed on the default branch (the branch was created in the dev's
separate clone). roboco_docs_write / commit failed BRANCH_MISMATCH and
the doc looped (i_am_blocked Not Found, give_me_work pointed at a dev
verb). Primary fix: claim_doc_task checks out the task branch into the
documenter's workspace. Facet (d): give_me_work's next-hint is now
role + status aware.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    repo = base["evidence_repo"]
    for m in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, m).return_value = []
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


# ---------------------------------------------------------------------------
# Facet (d): give_me_work next-hint is role + status aware
# ---------------------------------------------------------------------------


def _task(status: str) -> MagicMock:
    t = MagicMock()
    t.id = uuid4()
    t.status = status
    return t


def test_claim_verb_hint_doc_for_awaiting_documentation() -> None:
    hint = Choreographer._claim_verb_hint("documenter", _task("awaiting_documentation"))
    assert "claim_doc_task" in hint
    assert "i_will_work_on" not in hint


def test_claim_verb_hint_qa_for_awaiting_qa() -> None:
    hint = Choreographer._claim_verb_hint("qa", _task("awaiting_qa"))
    assert "claim_review" in hint
    assert "i_will_work_on" not in hint


def test_claim_verb_hint_pm_for_planning() -> None:
    hint = Choreographer._claim_verb_hint("cell_pm", _task("pending"))
    assert "i_will_plan" in hint


def test_claim_verb_hint_dev_default() -> None:
    hint = Choreographer._claim_verb_hint("developer", _task("pending"))
    assert "i_will_work_on" in hint


# ---------------------------------------------------------------------------
# Primary: claim_doc_task checks out the task branch into doc workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_doc_task_checks_out_branch() -> None:
    """After doc_claim, the task branch is checked out into the
    documenter's own clone so roboco_docs_write / commit don't
    BRANCH_MISMATCH."""
    doc_id = uuid4()
    task_id = uuid4()
    branch = "feature/backend/root1234--cellpm56--dev78901"

    t_initial = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        assigned_to=None,
        task_type="documentation",
        team="backend",
        branch_name=branch,
        quick_context=None,
        documents=[],
        commits=[{"sha": "abc123", "message": "[x] work"}],
        pr_number=7,
        pr_url="https://github.com/x/y/pull/7",
        dev_notes="done",
        acceptance_criteria_status=[],
        work_session_id=uuid4(),
    )
    t_claimed = MagicMock(
        **{
            **t_initial.__dict__,
            "assigned_to": doc_id,
            "status": "awaiting_documentation",
        }
    )

    task_svc = AsyncMock()
    task_svc.get.return_value = t_initial
    task_svc.agent_for.return_value = MagicMock(role="documenter", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.doc_claim.return_value = t_claimed
    git_svc = AsyncMock()
    git_svc.diff.return_value = "diff"
    git_svc.list_changed_files.return_value = ["README.md"]

    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    git_svc.checkout_branch_in_agent_workspace.assert_awaited_once_with(
        branch, actor_agent_id=doc_id
    )


@pytest.mark.asyncio
async def test_claim_doc_task_checkout_failure_does_not_break_claim() -> None:
    """A checkout hiccup must not fail the claim — the doc still gets
    an ok envelope and can retry / escalate from a claimed state."""
    doc_id = uuid4()
    task_id = uuid4()
    branch = "feature/backend/root1234--cellpm56--dev78901"
    t_initial = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        assigned_to=None,
        task_type="documentation",
        team="backend",
        branch_name=branch,
        quick_context=None,
        documents=[],
        commits=[{"sha": "abc", "message": "[x] w"}],
        pr_number=7,
        pr_url="u",
        dev_notes="d",
        acceptance_criteria_status=[],
        work_session_id=uuid4(),
    )
    t_claimed = MagicMock(**{**t_initial.__dict__, "assigned_to": doc_id})
    task_svc = AsyncMock()
    task_svc.get.return_value = t_initial
    task_svc.agent_for.return_value = MagicMock(role="documenter", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.doc_claim.return_value = t_claimed
    git_svc = AsyncMock()
    git_svc.checkout_branch_in_agent_workspace.side_effect = RuntimeError("fetch fail")
    git_svc.diff.return_value = ""
    git_svc.list_changed_files.return_value = []

    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.claim_doc_task(doc_id, task_id)
    # Claim still succeeds despite checkout raising.
    assert env.as_dict()["error"] is None
