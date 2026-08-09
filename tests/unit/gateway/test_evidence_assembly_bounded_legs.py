"""Bounded advisory-evidence legs on claim_review / claim_doc_task /
claim_gate_review / evidence() / i_am_done's success envelope.

Live bug: claim-evidence assembly's slow legs (branch-fetch-backed diff,
list_changed_files, the conventions-validator subprocess) had no per-leg
budget, so a hung leg silently ate the whole ``flow_verb_timeout_seconds``
(120s) and died as a FlowVerbTimeout 504 — holding every row the request
touched for the duration. Fix: each slow leg runs bounded via
``run_bounded_leg`` (``asyncio.wait_for``) against a SHARED ``LegBudget`` per
build; a timeout skips that piece, records a human-readable note in the
evidence's ``evidence_gaps``, and lets the claim verb succeed with partial
evidence instead of hanging.

Adversarial-review follow-up (round 2) covers four confirmed gaps:
1. ``run_bounded_leg`` must catch ``GitTimeoutError`` too (``_run_git``'s own
   internal subprocess bound — NOT a ``TimeoutError`` subclass, and usually
   the FIRST bound to trip since it defaults to 30s, shorter than a leg's
   own budget) — every timeout-shaped test below is parametrized over both
   exception shapes.
2. The conventions leg no longer wraps ``_qa_convention_findings`` in an
   outer ``run_bounded_leg`` — that raced ``conventions_check_for_task``'s
   own inner timeout+cleanup and leaked the validator subprocess. It now
   self-bounds via the ``timeout`` kwarg alone and reports its own gap.
3. ``fetch_branch_for_inspection`` takes an optional ``subprocess_timeout``
   so its fetch subprocess self-bounds near the leg's own budget instead of
   occupying a thread on the shared default executor for up to 300s.
4. A shared ``LegBudget`` (one per evidence build) makes every leg's
   ``wait_for`` draw from one TOTAL budget instead of getting its own full
   allotment — summed per-leg budgets can no longer exceed the total.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, TypeVar
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.exceptions import GitCommandError, GitTimeoutError
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer.evidence_legs import (
    LegBudget,
    run_bounded_leg,
)

_T = TypeVar("_T")

# Every timeout-shaped test is parametrized over both real timeout shapes:
# asyncio's own cancellation-converted TimeoutError, and GitTimeoutError
# (GitService._run_git's own internal subprocess bound — a GitError/
# RobocoError subclass, NOT a TimeoutError subclass, and the most common
# real-world single-hung-git-call shape since it defaults to a SHORTER
# window, 30s, than a leg's own budget).
_TIMEOUT_EXCEPTIONS = (
    TimeoutError("hung"),
    GitTimeoutError("git diff", 30),
)
_TIMEOUT_IDS = ("asyncio_timeout", "git_timeout")


# ---------------------------------------------------------------------------
# run_bounded_leg / LegBudget themselves
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_bounded_leg_passes_through_on_success() -> None:
    async def fast() -> str:
        return "value"

    gaps: list[str] = []
    result = await run_bounded_leg(
        fast(),
        default="fallback",
        budget=LegBudget(5.0),
        leg="unit leg",
        hint="check manually",
        task_id=uuid4(),
        gaps=gaps,
    )
    assert result == "value"
    assert gaps == []


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _TIMEOUT_EXCEPTIONS, ids=_TIMEOUT_IDS)
async def test_run_bounded_leg_degrades_to_default_on_timeout(exc: Exception) -> None:
    async def hangs() -> str:
        raise exc

    gaps: list[str] = []
    result = await run_bounded_leg(
        hangs(),
        default="fallback",
        budget=LegBudget(5.0),
        leg="unit leg",
        hint="check manually",
        task_id=uuid4(),
        gaps=gaps,
    )
    assert result == "fallback"
    assert len(gaps) == 1
    assert "unit leg unavailable" in gaps[0]
    assert "check manually" in gaps[0]


@pytest.mark.asyncio
async def test_run_bounded_leg_actually_bounds_a_slow_coroutine() -> None:
    """A genuinely slow (not pre-raised) coroutine is cancelled at the
    budget's deadline, not awaited to completion."""

    async def slow() -> str:
        await asyncio.sleep(10)
        return "too late"

    gaps: list[str] = []
    result = await run_bounded_leg(
        slow(),
        default="fallback",
        budget=LegBudget(0.05),
        leg="unit leg",
        hint="check manually",
        task_id=uuid4(),
        gaps=gaps,
    )
    assert result == "fallback"
    assert len(gaps) == 1


@pytest.mark.asyncio
async def test_run_bounded_leg_other_git_error_still_propagates() -> None:
    """A real command failure (not a timeout) is NOT a degrade case — it
    must still propagate uncaught, same as any other unexpected exception."""

    async def fails() -> str:
        raise GitCommandError("git diff", "fatal: bad revision")

    gaps: list[str] = []
    with pytest.raises(GitCommandError):
        await run_bounded_leg(
            fails(),
            default="fallback",
            budget=LegBudget(5.0),
            leg="unit leg",
            hint="check manually",
            task_id=uuid4(),
            gaps=gaps,
        )
    assert gaps == []


def test_leg_budget_remaining_shrinks_over_time() -> None:
    # Total well above _MIN_LEG_SECONDS (1.0) so the floor never engages
    # here — otherwise both readings would clamp to 1.0 and look equal.
    budget = LegBudget(3.0)
    first = budget.remaining()
    time.sleep(0.1)
    second = budget.remaining()
    assert second < first
    assert first == pytest.approx(3.0, abs=0.05)
    assert (first - second) == pytest.approx(0.1, abs=0.05)


def test_leg_budget_floors_at_minimum() -> None:
    """A budget already past its deadline still yields a positive window
    (the floor) rather than 0 or a negative timeout — a leg always gets a
    real chance to run, even a badly-exhausted one."""
    budget = LegBudget(0.01)
    time.sleep(0.05)
    assert budget.remaining() == pytest.approx(1.0, abs=0.05)


@pytest.mark.asyncio
async def test_run_bounded_leg_shares_one_shrinking_budget_across_legs() -> None:
    """Three legs sharing ONE LegBudget: the first two finish fast and
    consume real budget; the last two never finish on their own and get
    progressively SMALLER windows (shrinking, not each getting the full
    total) — both record their own gap, and total wall time stays bounded
    near the shared total instead of the naive per-leg sum (0.1+0.1+5+5s).
    """
    budget = LegBudget(1.2)
    gaps: list[str] = []

    async def _takes(seconds: float) -> str:
        await asyncio.sleep(seconds)
        return "done"

    start = time.monotonic()
    remaining_before_1 = budget.remaining()
    r1 = await run_bounded_leg(
        _takes(0.1),
        default="gap1",
        budget=budget,
        leg="leg1",
        hint="h",
        task_id="t",
        gaps=gaps,
    )
    remaining_before_2 = budget.remaining()
    r2 = await run_bounded_leg(
        _takes(5.0),
        default="gap2",
        budget=budget,
        leg="leg2",
        hint="h",
        task_id="t",
        gaps=gaps,
    )
    remaining_before_3 = budget.remaining()
    r3 = await run_bounded_leg(
        _takes(5.0),
        default="gap3",
        budget=budget,
        leg="leg3",
        hint="h",
        task_id="t",
        gaps=gaps,
    )
    elapsed = time.monotonic() - start
    expected_gap_count = 2  # leg2 + leg3 both timed out; leg1 completed

    assert r1 == "done"
    assert r2 == "gap2"
    assert r3 == "gap3"
    assert len(gaps) == expected_gap_count
    assert "leg2" in gaps[0]
    assert "leg3" in gaps[1]
    # Each leg's own remaining() reading is strictly smaller than the last
    # — the shared deadline never resets.
    assert remaining_before_1 > remaining_before_2 > remaining_before_3
    # ponytail: the floor (max 1.0s) can inflate the LAST leg's window past
    # what a naive "budget minus elapsed" would give once the deadline is
    # already exhausted — a single floor engagement caps the worst-case
    # overage at _MIN_LEG_SECONDS (1.0s), so budget total + ~1.3s is a safe,
    # honest ceiling rather than a strict `<= budget` bound. Upgrade path:
    # make the floor configurable if a caller ever needs a tighter cap.
    budget_total_seconds = 1.2
    floor_overage_tolerance_seconds = 1.3
    assert elapsed <= budget_total_seconds + floor_overage_tolerance_seconds
    # And it's nowhere near the naive per-leg-gets-its-own-full-timeout sum
    # (0.1 + 5.0 + 5.0 = 10.1s) that pre-LegBudget behavior would produce.
    naive_per_leg_sum_seconds = 5.0
    assert elapsed < naive_per_leg_sum_seconds


# ---------------------------------------------------------------------------
# Shared choreographer test harness (mirrors test_choreographer_qa.py /
# test_claim_doc_task_checkout.py / test_claim_gate_review_guards.py)
# ---------------------------------------------------------------------------


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
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _stub_empty_ledger(session: MagicMock) -> None:
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )


# ---------------------------------------------------------------------------
# claim_review (qa.py)
# ---------------------------------------------------------------------------

_PR_NUMBER = 8
_PR_URL = "https://github.com/x/y/pull/8"


def _qa_task(task_id: Any) -> MagicMock:
    return MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=None,
        pr_number=_PR_NUMBER,
        pr_url=_PR_URL,
        commits=[{"sha": "abc123", "message": "feat: x"}],
        team="backend",
        branch_name="feature/backend/abc--def",
        work_session_id=uuid4(),
        documents=[],
        dev_notes="implemented x",
        acceptance_criteria=["AC1"],
        acceptance_criteria_status=[
            {"criterion": "AC1", "referencing_artifact_id": "abc123"},
        ],
        parent_task_id=None,
    )


def _qa_harness(git_svc: AsyncMock, **overrides: Any) -> tuple[Choreographer, Any, Any]:
    """Does NOT touch ``settings.conventions_enabled`` — callers that care
    set it themselves via their own ``monkeypatch`` fixture; a shared
    forced-False here would silently clobber a caller's forced-True set
    moments earlier (``monkeypatch.setattr`` doesn't stack, last write
    wins), which is exactly what broke the conventions-specific tests.

    ``**overrides`` forwards to ``_make_deps`` (e.g. a custom
    ``evidence_repo`` for the latency-gather test below)."""
    qa_id = uuid4()
    task_id = uuid4()
    t_initial = _qa_task(task_id)
    t_claimed = MagicMock(**{**t_initial.__dict__, "assigned_to": qa_id})

    task_svc = AsyncMock()
    task_svc.get.return_value = t_initial
    task_svc.agent_for.return_value = MagicMock(role="qa", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.qa_claim.return_value = t_claimed
    _stub_empty_ledger(task_svc.session)

    deps = _make_deps(task=task_svc, git=git_svc, **overrides)
    return Choreographer(deps), qa_id, task_id


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _TIMEOUT_EXCEPTIONS, ids=_TIMEOUT_IDS)
async def test_claim_review_diff_timeout_degrades_with_gap(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """A hung git.diff_and_files on claim_review must not hang the verb: the
    combined diff+files leg degrades to an empty diff AND an empty
    files_changed together (one resolution, one leg) and records the gap."""
    monkeypatch.setattr(settings, "conventions_enabled", False)
    git_svc = AsyncMock()
    git_svc.diff_and_files.side_effect = exc
    c, qa_id, task_id = _qa_harness(git_svc)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff_summary"] == ""
    assert ev["files_changed"] == []
    assert "evidence_gaps" in ev
    assert len(ev["evidence_gaps"]) == 1
    # A combined-leg timeout kills diff AND files_changed together — the
    # gap note names both losses, not just "pr diff", so a reader can tell
    # files_changed is empty because of a timeout, not genuinely empty.
    assert "pr diff + files_changed unavailable" in ev["evidence_gaps"][0]


@pytest.mark.asyncio
async def test_claim_review_conventions_timeout_degrades_with_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """conventions_check_for_task's OWN internal timeout (proc.kill()'d and
    reaped inside git.py, never raising) surfaces as could_not_run=True with
    a "timed out" reason — not an exception. The advisory call site
    (_qa_convention_findings) detects that shape and records the gap
    itself; NO outer run_bounded_leg wraps this leg (that's the fix — see
    module docstring point 2)."""
    monkeypatch.setattr(settings, "conventions_enabled", True)
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("diff content", ["README.md"])
    git_svc.conventions_check_for_task.return_value = {
        "findings": [],
        "could_not_run": True,
        "reason": "validator timed out after 30.0s",
    }
    c, qa_id, task_id = _qa_harness(git_svc)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff_summary"] == "diff content"
    assert ev["files_changed"] == ["README.md"]
    assert ev["convention_findings"] == [
        {"could_not_run": True, "reason": "validator timed out after 30.0s"}
    ]
    assert "evidence_gaps" in ev
    assert any("conventions findings unavailable" in g for g in ev["evidence_gaps"])
    # The advisory (shorter) ceiling reached the validator call, not the
    # fail-closed i_am_done/pr_pass default (None -> hardcoded 120s).
    git_svc.conventions_check_for_task.assert_awaited_once()
    call_kwargs = git_svc.conventions_check_for_task.await_args.kwargs
    assert (
        call_kwargs["timeout"]
        <= settings.conventions_validator_advisory_timeout_seconds
    )
    assert call_kwargs["timeout"] > 0
    assert call_kwargs["changed_files"] == ["README.md"]


@pytest.mark.asyncio
async def test_claim_review_conventions_non_timeout_could_not_run_no_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine resolution failure (not a timeout) still surfaces in
    convention_findings (existing fail-open shape) but must NOT also spam
    evidence_gaps — that's reserved for actual degraded-advisory-leg notes."""
    monkeypatch.setattr(settings, "conventions_enabled", True)
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("diff content", ["README.md"])
    git_svc.conventions_check_for_task.return_value = {
        "findings": [],
        "could_not_run": True,
        "reason": "resolution failed: NotFoundError: Branch not found",
    }
    c, qa_id, task_id = _qa_harness(git_svc)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["convention_findings"][0]["could_not_run"] is True
    assert "evidence_gaps" not in ev


@pytest.mark.asyncio
async def test_qa_convention_findings_not_cancelled_by_outer_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the orphaned-subprocess bug: even with a tiny shared
    evidence-assembly budget, _qa_convention_findings must NOT be cut short
    by an outer wait_for — it awaits conventions_check_for_task to
    completion. A slow-but-real mock (0.15s) run against a budget whose
    total is far smaller (0.02s) proves there is no outer wrap: if there
    still were one, this would return the default/empty shape instead of
    the real result."""
    monkeypatch.setattr(settings, "conventions_enabled", True)

    async def _slow_check(*_args: object, **_kwargs: object) -> dict[str, Any]:
        await asyncio.sleep(0.15)
        return {"findings": [{"file": "x.py", "line": 1}], "could_not_run": False}

    git_svc = AsyncMock()
    git_svc.conventions_check_for_task.side_effect = _slow_check
    c, _qa_id, _task_id = _qa_harness(git_svc)
    cc: Any = c

    gaps: list[str] = []
    result = await cc._qa_convention_findings(
        uuid4(), MagicMock(), timeout=0.02, gaps=gaps
    )
    assert result == [{"file": "x.py", "line": 1}]
    assert gaps == []


@pytest.mark.asyncio
async def test_claim_review_normal_path_has_no_evidence_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Byte-for-byte unchanged normal path: no evidence_gaps key at all when
    nothing times out."""
    monkeypatch.setattr(settings, "conventions_enabled", False)
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("diff content", ["README.md"])
    c, qa_id, task_id = _qa_harness(git_svc)

    env = await c.claim_review(qa_id, task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff_summary"] == "diff content"
    assert ev["files_changed"] == ["README.md"]
    assert "evidence_gaps" not in ev


@pytest.mark.asyncio
async def test_claim_review_awaits_git_leg_sequential_to_db_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The combined git leg (diff_and_files) and the two independent DB
    reads (journal highlights, ancestor context) must NEVER overlap:
    ``self.task``/``self.git``/``self.evidence_repo`` share ONE
    request-scoped ``AsyncSession`` (see ``deps.py``), and the git leg's own
    workspace/token resolution runs DB lookups against that same session, so
    gathering it alongside the DB reads risks two queries racing one
    ``AsyncSession`` (unsupported by SQLAlchemy). Proven structurally via an
    in-flight counter (peak concurrency never exceeds 1) rather than a
    wall-clock margin, which can flake under CI load."""
    monkeypatch.setattr(settings, "conventions_enabled", False)
    leg_seconds = 0.02
    in_flight = 0
    peak = 0

    async def _track(result: _T) -> _T:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(leg_seconds)
        in_flight -= 1
        return result

    async def _diff_leg(*_args: object, **_kwargs: object) -> Any:
        return await _track(("diff content", ["README.md"]))

    async def _journal_leg(*_args: object, **_kwargs: object) -> list[Any]:
        return await _track([])

    async def _ancestor_leg(*_args: object, **_kwargs: object) -> list[Any]:
        return await _track([])

    git_svc = AsyncMock()
    git_svc.diff_and_files.side_effect = _diff_leg

    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.side_effect = _journal_leg
    evidence_repo.ancestor_context_for_task.side_effect = _ancestor_leg

    c, qa_id, task_id = _qa_harness(git_svc, evidence_repo=evidence_repo)

    env = await c.claim_review(qa_id, task_id)

    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff_summary"] == "diff content"
    assert ev["files_changed"] == ["README.md"]
    # Structural proof: the three legs never overlap in-flight.
    assert peak == 1


# ---------------------------------------------------------------------------
# claim_doc_task (doc.py)
# ---------------------------------------------------------------------------


def _doc_task(task_id: Any, branch: str) -> MagicMock:
    return MagicMock(
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


def _doc_harness(git_svc: AsyncMock) -> tuple[Choreographer, Any, Any]:
    doc_id = uuid4()
    task_id = uuid4()
    branch = "feature/backend/root1234--cellpm56--dev78901"
    t_initial = _doc_task(task_id, branch)
    t_claimed = MagicMock(**{**t_initial.__dict__, "assigned_to": doc_id})

    task_svc = AsyncMock()
    task_svc.get.return_value = t_initial
    task_svc.agent_for.return_value = MagicMock(role="documenter", team="backend")
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.doc_claim.return_value = t_claimed
    _stub_empty_ledger(task_svc.session)

    deps = _make_deps(task=task_svc, git=git_svc)
    return Choreographer(deps), doc_id, task_id


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _TIMEOUT_EXCEPTIONS, ids=_TIMEOUT_IDS)
async def test_claim_doc_task_diff_timeout_degrades_with_gap(exc: Exception) -> None:
    git_svc = AsyncMock()
    git_svc.diff.side_effect = exc
    git_svc.list_changed_files.return_value = ["README.md"]
    c, doc_id, task_id = _doc_harness(git_svc)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff_summary"] == ""
    assert ev["files_changed"] == ["README.md"]
    assert "evidence_gaps" in ev
    assert any("pr diff unavailable" in g for g in ev["evidence_gaps"])


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _TIMEOUT_EXCEPTIONS, ids=_TIMEOUT_IDS)
async def test_claim_doc_task_checkout_timeout_degrades_with_gap(
    exc: Exception,
) -> None:
    """The checkout leg (run before evidence assembly) also degrades bounded
    instead of an unbounded suppress(Exception) — its gap folds into the
    same evidence_gaps list the diff/list_changed_files legs use, drawing
    from the SAME shared LegBudget."""
    git_svc = AsyncMock()
    git_svc.checkout_branch_in_agent_workspace.side_effect = exc
    git_svc.diff.return_value = "diff content"
    git_svc.list_changed_files.return_value = ["README.md"]
    c, doc_id, task_id = _doc_harness(git_svc)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    # The other legs are untouched by the checkout's own timeout.
    assert ev["pr_diff_summary"] == "diff content"
    assert ev["files_changed"] == ["README.md"]
    assert "evidence_gaps" in ev
    assert any("workspace checkout unavailable" in g for g in ev["evidence_gaps"])


@pytest.mark.asyncio
async def test_claim_doc_task_normal_path_has_no_evidence_gaps() -> None:
    git_svc = AsyncMock()
    git_svc.diff.return_value = "diff content"
    git_svc.list_changed_files.return_value = ["README.md"]
    c, doc_id, task_id = _doc_harness(git_svc)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff_summary"] == "diff content"
    assert ev["files_changed"] == ["README.md"]
    assert "evidence_gaps" not in ev


# ---------------------------------------------------------------------------
# claim_gate_review (pr_gate.py)
# ---------------------------------------------------------------------------


def _gate_task() -> MagicMock:
    return MagicMock(
        id=uuid4(),
        status="awaiting_pr_review",
        assigned_to=uuid4(),
        parent_task_id=None,
        task_type="planning",
        dependency_ids=[],
        team="main_pm",
        pr_number=139,
        pr_url="https://example/pr/139",
        branch_name="feature/main_pm/root",
        batch_id=None,
        description=None,
        acceptance_criteria=[],
    )


def _gate_harness(git_svc: AsyncMock) -> tuple[Choreographer, Any, Any]:
    task_svc = AsyncMock()
    t = _gate_task()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        role="pr_reviewer", slug="be-pr-reviewer"
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    task_svc.pr_gate_claim = AsyncMock(return_value=t)
    _stub_empty_ledger(task_svc.session)
    deps = _make_deps(task=task_svc, git=git_svc)
    return Choreographer(deps), t, uuid4()


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _TIMEOUT_EXCEPTIONS, ids=_TIMEOUT_IDS)
async def test_claim_gate_review_diff_timeout_degrades_with_gap(exc: Exception) -> None:
    git_svc = AsyncMock()
    git_svc.diff.side_effect = exc
    git_svc.list_changed_files.return_value = ["README.md"]
    c, t, reviewer_id = _gate_harness(git_svc)

    env = await c.claim_gate_review(reviewer_id, t.id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff"] == ""
    assert "evidence_gaps" in ev
    assert any("pr diff unavailable" in g for g in ev["evidence_gaps"])


@pytest.mark.asyncio
async def test_claim_gate_review_files_changed_timeout_degrades_with_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_changed_files hanging must not sink the whole gate claim, and the
    diff leg (which succeeded) must remain intact in the evidence.

    ``_gate_changed_files`` has its own internal ``except Exception`` (an
    existing hard-failure fail-open, untouched by this fix) that would
    swallow a synchronously-raised exception (of either timeout shape)
    before the outer ``run_bounded_leg`` ever saw it — so this uses a
    genuinely slow coroutine + a monkeypatched short budget to exercise the
    real cancel-at-the-wall path (``asyncio.wait_for`` cancelling the
    awaited task via ``CancelledError``, which that ``except Exception``
    does NOT catch), matching what a real hang does in production.
    """
    monkeypatch.setattr(settings, "evidence_assembly_timeout_seconds", 0.02)

    async def _hangs(*_args: object, **_kwargs: object) -> list[str]:
        await asyncio.sleep(5)
        return ["should-not-be-reached"]

    git_svc = AsyncMock()
    git_svc.diff.return_value = "diff content"
    git_svc.list_changed_files.side_effect = _hangs
    c, t, reviewer_id = _gate_harness(git_svc)

    env = await c.claim_gate_review(reviewer_id, t.id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff"] == "diff content"
    assert "evidence_gaps" in ev
    assert any("files_changed unavailable" in g for g in ev["evidence_gaps"])


@pytest.mark.asyncio
async def test_claim_gate_review_normal_path_has_no_evidence_gaps() -> None:
    git_svc = AsyncMock()
    git_svc.diff.return_value = "diff content"
    git_svc.list_changed_files.return_value = ["README.md"]
    c, t, reviewer_id = _gate_harness(git_svc)

    env = await c.claim_gate_review(reviewer_id, t.id)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["pr_diff"] == "diff content"
    assert "evidence_gaps" not in ev


# ---------------------------------------------------------------------------
# _build_i_am_done_ok (i_am_done's success-envelope evidence). Runs strictly
# AFTER the composed transition already committed — advisory, not gating —
# so its list_changed_files leg is bounded exactly like the claim paths.
# ---------------------------------------------------------------------------


def _done_task(task_id: Any) -> MagicMock:
    return MagicMock(
        id=task_id,
        branch_name="feature/backend/abc",
        commits=[{"sha": "abc123", "message": "x"}],
        dev_notes="done",
        acceptance_criteria_status=[],
        pr_number=5,
        pr_url="https://github.com/x/y/pull/5",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _TIMEOUT_EXCEPTIONS, ids=_TIMEOUT_IDS)
async def test_build_i_am_done_ok_files_changed_timeout_degrades_with_gap(
    exc: Exception,
) -> None:
    """A hung list_changed_files leg in i_am_done's already-committed
    success-envelope builder must not hang the dev's response — it degrades
    to an empty files_changed and records the gap."""
    agent_id = uuid4()
    task_id = uuid4()
    t = _done_task(task_id)
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    _stub_empty_ledger(task_svc.session)
    git_svc = AsyncMock()
    git_svc.list_changed_files.side_effect = exc
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c._build_i_am_done_ok(agent_id, task_id, t)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["files_changed"] == []
    assert "evidence_gaps" in ev
    assert any("files_changed unavailable" in g for g in ev["evidence_gaps"])


@pytest.mark.asyncio
async def test_build_i_am_done_ok_normal_path_has_no_evidence_gaps() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = _done_task(task_id)
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    _stub_empty_ledger(task_svc.session)
    git_svc = AsyncMock()
    git_svc.list_changed_files.return_value = ["README.md"]
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c._build_i_am_done_ok(agent_id, task_id, t)
    body = env.as_dict()
    assert body["error"] is None, body
    ev = body["evidence"]
    assert ev["files_changed"] == ["README.md"]
    assert "evidence_gaps" not in ev
