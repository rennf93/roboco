"""The unchanged-PR re-submit loop-stopper, applied to ``submit_up`` (cell→root).

Refuses to re-open the gate when the cell PR's head SHA equals the SHA the
last ``pr_fail`` recorded (no new dev work landed); ambiguous cases FAIL OPEN.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

SHA_OLD = "aaaa1111bbbb2222cccc3333dddd4444eeee5555"
SHA_NEW = "9999888877776666555544443333222211110000"


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
    base["journal"].has_decision_for_task.return_value = True
    base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    base["journal"].has_reflect_for_task.return_value = True
    return ChoreographerDeps(**base)


def _resubmit_cell(
    *,
    notes_structured: dict[str, Any] | None,
    pr_number: int | None = 132,
) -> tuple[Choreographer, Any, Any]:
    """A cell-PM task re-submitted from ``in_progress`` after a ``pr_fail``.

    Mirrors a live cell re-submit: the cell task is back in ``in_progress``
    (re-claimed out of ``needs_revision``), carries the prior ``pr_fail``
    verdict in ``notes_structured.pr_review``, and the cell→root PR is still
    open. The ``_submit_up_guard`` preflight is satisfied (owned by the cell
    PM, journal decision, subtasks terminal, branch present, notes long
    enough) so the unchanged-PR gate is the thing under test.
    """
    cell_pm_id = uuid4()
    cell_task_id = uuid4()
    in_prog = MagicMock(
        id=cell_task_id,
        status="in_progress",
        assigned_to=cell_pm_id,
        pr_number=pr_number,
        branch_name="feature/backend/cell-task",
        parent_task_id=uuid4(),
        batch_id=None,
        team="backend",
        notes_structured=notes_structured,
    )
    gated = MagicMock(**{**in_prog.__dict__, "status": "awaiting_pr_review"})
    task_svc = AsyncMock()
    task_svc.get.return_value = in_prog
    task_svc.submit_for_review.return_value = gated
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.uncovered_parent_acceptance_criteria.return_value = []
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )
    c = Choreographer(_make_deps(task=task_svc, git=AsyncMock()))
    cc: Any = c
    cc._project_slug_for = AsyncMock(return_value="proj-slug")
    return c, cell_pm_id, cell_task_id


@pytest.mark.asyncio
async def test_submit_up_refuses_unchanged_pr_after_pr_fail() -> None:
    """The loop-stopper still holds past the one-shot exemption: prior
    pr_fail stamped head SHA X, the cell PR head is still X (no new dev work
    on the cell branch). The findings ledger here fail-opens to "nothing
    open" (mock session, no real query) — the same signal
    ``_check_submit_up_gates`` upstream already reads for FINDINGS_ADDRESSED
    — so the first resubmit at this head is the one-shot exemption (see
    test_resubmit_unchanged_head_exemption.py for full exemption coverage);
    a second resubmit at the SAME head refuses, so the loop still can't run
    forever."""
    c, cell_pm_id, cell_task_id = _resubmit_cell(
        notes_structured={
            "pr_review": {"verdict": "failed", "head_sha": SHA_OLD, "summary": "..."}
        }
    )
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_OLD)

    first = await c.submit_up(
        cell_pm_id, cell_task_id, notes="re-submitting the cell after the fix"
    )
    assert first.error is None, first.as_dict()

    env = await c.submit_up(
        cell_pm_id, cell_task_id, notes="re-submitting again; still unchanged"
    )

    assert env.error is not None, env.as_dict()
    assert env.error == "invalid_state", env.as_dict()
    assert "unchanged" in (env.message or "").lower()
    remediate = env.remediate or ""
    assert "submit_up" in remediate
    # The second attempt's PR was NOT re-opened / re-pushed — the runner ran
    # only for the first (exempted) call.
    c.task.submit_for_review.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_up_allows_after_cell_branch_advanced() -> None:
    """A different current head SHA ⇒ dev work landed on the cell branch ⇒ the
    diff changed ⇒ allow the re-submit into the gate."""
    c, cell_pm_id, cell_task_id = _resubmit_cell(
        notes_structured={
            "pr_review": {"verdict": "failed", "head_sha": SHA_OLD, "summary": "..."}
        }
    )
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_NEW)

    env = await c.submit_up(
        cell_pm_id, cell_task_id, notes="re-submitting after the dev re-assembly"
    )

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"
    c.task.submit_for_review.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_up_fail_open_when_no_prior_pr_fail_verdict() -> None:
    """No pr_review (first submit) ⇒ nothing to compare ⇒ allow."""
    c, cell_pm_id, cell_task_id = _resubmit_cell(notes_structured=None)
    env = await c.submit_up(
        cell_pm_id, cell_task_id, notes="first cell submit; nothing to compare yet"
    )
    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"


# ---------------------------------------------------------------------------
# submit_for_review returns None when the task raced out of in_progress after
# create_pr already opened the cell→root PR; the remediate must tell the PM the
# PR is open so the orphan is recoverable via create_pr's idempotent re-issue.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_up_none_remediate_names_the_already_open_pr() -> None:
    """submit_for_review returns None (raced out of in_progress) AFTER create_pr
    already opened the cell→root PR. The remediate must name the open PR and point
    the PM at re-fetching + reconciling, not the misleading 'PR ready' hint."""
    c, cell_pm_id, cell_task_id = _resubmit_cell(notes_structured=None)
    # A concurrent transition (stale-heartbeat reaper unclaim, or a racing
    # i_am_blocked) moved the task out of in_progress between the precondition
    # gate and the runner's composed action → submit_for_review returns None.
    c.task.submit_for_review.return_value = None

    env = await c.submit_up(
        cell_pm_id, cell_task_id, notes="submitting the assembled cell scope"
    )

    assert env.error == "invalid_state", env.as_dict()
    remediate = (env.remediate or "").lower()
    # The create_pr pre-side-effect ran BEFORE the None transition, so the PR
    # is open on GitHub — the remediate must say so. The old 'PR ready' hint
    # hid this.
    assert "pr" in remediate and "open" in remediate, env.as_dict()
    # The misleading old hint is gone.
    assert "pr ready" not in remediate, env.as_dict()
    # And the PR really was opened (pre-side-effect ran before the None).
    c.git.create_pr.assert_awaited_once()
