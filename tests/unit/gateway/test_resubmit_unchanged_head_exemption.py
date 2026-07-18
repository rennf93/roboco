"""The one-shot resubmit exemption on the unchanged-PR loop-stopper.

Live wedge: a cell task looped block/unblock for hours because the freshness
guard (``_unchanged_pr_guard``, shared by ``submit_root``/``submit_up``)
demands a new commit after ANY ``pr_fail`` — a structural deadlock when the
rejection round's findings require no code change (e.g. a transient
CI-lookup error ledgered as a finding, then waived/addressed). Once every
ledger finding is resolved (``_open_finding_ids`` empty), ONE resubmission is
exempted per head sha (``markers.resubmit_unchanged_head``); a second attempt
at the SAME head still refuses. Covers both call sites: ``submit_root``
(root) and ``submit_up`` (cell).

Note on test style: the upstream ``FINDINGS_ADDRESSED`` tracing gate
(``_check_submit_up_gates``, shared by both verbs) already refuses the whole
verb with ``tracing_gap`` whenever findings are open — by the time
``_unchanged_pr_guard`` runs, open findings are already impossible through the
public ``submit_root``/``submit_up`` entrypoints. The "findings still open"
and "ambiguous case short-circuits before the findings check" scenarios are
therefore exercised by calling the guard method directly (defense-in-depth on
the guard's own logic); the exemption-grant and one-shot-per-head scenarios
drive the full verb end-to-end to prove the real wiring.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
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


def _unchanged_notes(head_sha: str = SHA_OLD) -> dict[str, Any]:
    return {"pr_review": {"verdict": "failed", "head_sha": head_sha, "summary": "..."}}


async def _call_guard(
    c: Choreographer, kind: str, t: Any, briefing: dict[str, Any]
) -> Any:
    if kind == "root":
        return await c._submit_root_unchanged_pr_guard(t, briefing)
    return await c._submit_up_unchanged_pr_guard(t, briefing)


# ---------------------------------------------------------------------------
# Direct guard tests — isolate ``_unchanged_pr_guard`` from the upstream
# FINDINGS_ADDRESSED gate, which already forbids open findings from ever
# reaching here through the real submit_root/submit_up flow.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["root", "cell"])
async def test_refused_when_findings_still_open(kind: str) -> None:
    """Unchanged head + open findings -> refused (behavior unchanged)."""
    c = Choreographer(_make_deps())
    cc: Any = c
    cc._current_pr_head_sha = AsyncMock(return_value=SHA_OLD)
    cc._open_finding_ids = AsyncMock(return_value=("abcd1234",))
    t = MagicMock(
        id=uuid4(), notes_structured=_unchanged_notes(), orchestration_markers=None
    )

    env = await _call_guard(c, kind, t, {})

    assert env is not None
    assert env.error == "invalid_state", env.as_dict()
    assert "unchanged" in (env.message or "").lower()
    assert markers.get_resubmit_unchanged_head(t) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["root", "cell"])
async def test_allowed_when_head_advanced_before_findings_are_even_checked(
    kind: str,
) -> None:
    """A different current head SHA -> allowed; the ambiguity check short-
    circuits before the findings/exemption logic ever runs."""
    c = Choreographer(_make_deps())
    cc: Any = c
    cc._current_pr_head_sha = AsyncMock(return_value=SHA_NEW)
    open_findings_spy = AsyncMock(return_value=("abcd1234",))
    cc._open_finding_ids = open_findings_spy
    t = MagicMock(
        id=uuid4(), notes_structured=_unchanged_notes(), orchestration_markers=None
    )

    env = await _call_guard(c, kind, t, {})

    assert env is None
    open_findings_spy.assert_not_awaited()


# ---------------------------------------------------------------------------
# End-to-end tests — drive the real submit_root/submit_up verb to prove the
# exemption's marker stamp actually lets the assembled PR proceed into the
# gate. Zero open findings satisfies both the upstream FINDINGS_ADDRESSED gate
# and the new exemption check, mirroring test_submit_root_unchanged_pr_guard.py
# / test_submit_up_unchanged_pr_guard.py's fixture shape.
# ---------------------------------------------------------------------------


def _resubmit(
    kind: str,
    *,
    notes_structured: dict[str, Any] | None,
    pr_number: int | None = 139,
) -> tuple[Choreographer, Any, Any]:
    pm_id = uuid4()
    task_id = uuid4()
    if kind == "root":
        role, team, parent = "main_pm", "main_pm", None
        branch = "feature/main_pm/c80e19ff"
    else:
        role, team, parent = "cell_pm", "backend", uuid4()
        branch = "feature/backend/cell-task"
    in_prog = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=pm_id,
        pr_number=pr_number,
        branch_name=branch,
        parent_task_id=parent,
        batch_id=None,
        team=team,
        notes_structured=notes_structured,
        orchestration_markers=None,
    )
    gated = MagicMock(**{**in_prog.__dict__, "status": "awaiting_pr_review"})
    task_svc = AsyncMock()
    task_svc.get.return_value = in_prog
    task_svc.submit_for_review.return_value = gated
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.uncovered_parent_acceptance_criteria.return_value = []
    task_svc.agent_for.return_value = MagicMock(role=role, team=team)
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )
    c = Choreographer(_make_deps(task=task_svc, git=AsyncMock()))
    cc: Any = c
    cc._project_slug_for = AsyncMock(return_value="proj-slug")
    # Zero open findings throughout — satisfies the upstream FINDINGS_ADDRESSED
    # gate so the flow reaches the unchanged-PR guard under test.
    cc._open_finding_ids = AsyncMock(return_value=())
    return c, pm_id, task_id


async def _call_submit(
    c: Choreographer, kind: str, pm_id: Any, task_id: Any, notes: str
) -> Any:
    if kind == "root":
        return await c.submit_root(pm_id, task_id, notes=notes)
    return await c.submit_up(pm_id, task_id, notes=notes)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["root", "cell"])
async def test_exemption_granted_when_no_open_findings_and_no_prior_marker(
    kind: str,
) -> None:
    """Unchanged head + zero open findings + no marker -> allowed once, marker
    stamped with the current head."""
    c, pm_id, task_id = _resubmit(kind, notes_structured=_unchanged_notes())
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_OLD)

    env = await _call_submit(
        c, kind, pm_id, task_id, "re-submitting; CI blip only, no code change needed"
    )

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"
    c.task.submit_for_review.assert_awaited_once()
    t = await c.task.get(task_id)
    assert markers.get_resubmit_unchanged_head(t) == SHA_OLD


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["root", "cell"])
async def test_second_attempt_at_same_head_is_refused(kind: str) -> None:
    """The exemption is one-shot: a second resubmit at the SAME head, still
    with no open findings, refuses — the marker already recorded this head."""
    c, pm_id, task_id = _resubmit(kind, notes_structured=_unchanged_notes())
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_OLD)

    first = await _call_submit(c, kind, pm_id, task_id, "first resubmit; CI blip only")
    assert first.error is None, first.as_dict()

    second = await _call_submit(
        c, kind, pm_id, task_id, "second resubmit; still the same unchanged head"
    )
    assert second.error == "invalid_state", second.as_dict()
    assert "unchanged" in (second.message or "").lower()
    assert "already used" in (second.message or "").lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["root", "cell"])
async def test_allowed_when_head_advanced(kind: str) -> None:
    """A different current head SHA -> allowed (existing fail-open behavior),
    end to end through the real verb."""
    c, pm_id, task_id = _resubmit(kind, notes_structured=_unchanged_notes())
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_NEW)

    env = await _call_submit(
        c, kind, pm_id, task_id, "resubmitting after the real fix landed"
    )

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"
    c.task.submit_for_review.assert_awaited_once()
