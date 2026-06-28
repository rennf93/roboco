"""F007 — the unchanged-PR re-submit loop-stopper is root-only; ``submit_up``
(cell→root) had no head_sha guard, so a weak cell PM could re-submit the
unchanged cell PR and loop ``awaiting_pr_review`` → ``pr_fail`` forever
(the cell-level analogue of the 2026-06-27 root loop F016 closes).

``pr_fail`` stamps the assembled PR's head SHA into
``notes_structured.pr_review.head_sha`` for BOTH cell and root gate tasks
(``pr_gate._capture_pr_head_sha`` / ``_record_gate_verdict`` are
gate-verb-level, not root-level). So the same structural refusal applies
to ``submit_up``: if the cell PR's current head SHA equals the SHA the
last ``pr_fail`` recorded, no new dev work landed on the cell branch ⇒
the diff is byte-identical ⇒ refuse, do not re-open the gate. Every
ambiguous case FAILS OPEN, identical to the root guard (shared
``_current_pr_head_sha``).
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
    """The loop-stopper: prior pr_fail stamped head SHA X, the cell PR head is
    still X (no new dev work on the cell branch) → refuse, do not re-open the
    gate."""
    c, cell_pm_id, cell_task_id = _resubmit_cell(
        notes_structured={
            "pr_review": {"verdict": "failed", "head_sha": SHA_OLD, "summary": "..."}
        }
    )
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_OLD)

    env = await c.submit_up(
        cell_pm_id, cell_task_id, notes="re-submitting the cell after the fix"
    )

    assert env.error is not None, env.as_dict()
    assert env.error == "invalid_state", env.as_dict()
    assert "unchanged" in (env.message or "").lower()
    remediate = env.remediate or ""
    assert "submit_up" in remediate
    # The cell PR was NOT re-opened / re-pushed — the runner never ran.
    c.task.submit_for_review.assert_not_awaited()


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
