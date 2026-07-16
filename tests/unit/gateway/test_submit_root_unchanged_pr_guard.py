"""The hard ``submit_root`` unchanged-PR gate — the pr_fail re-submit loop-stopper.

The 2026-06-27 infinite ``pr_fail`` loop: a Main-PM-owned root (S1
"chart-first Metrics", PR #139) was ``pr_fail``'d for a real code defect, routed
to ``needs_revision``, the Main PM re-claimed + re-delegated nothing, and
re-submitted the **unchanged** root → ``awaiting_pr_review`` → ``pr_fail`` again,
forever. The prior fixes (the ``pr_fail`` a2a steer + the ``next_hint`` "do NOT
re-submit") are *hints* — a weak coordinator (minimax-m3:cloud) ignored them and
re-submitted PR #139 byte-identical. Hints do not stop a model that won't read
them; only a structural refusal does.

This gate refuses the re-submit when the assembled root PR's head SHA is
unchanged since the last ``pr_fail`` (no new cell work landed on the root
branch). ``pr_fail`` stamps that SHA into ``notes_structured.pr_review.head_sha``
(``_capture_pr_head_sha`` + ``_record_gate_verdict``); ``submit_root`` reads it
back and compares against the PR's current head SHA. Equal ⇒ refuse; different
⇒ the branch advanced ⇒ allow. Every ambiguous case FAILS OPEN (no prior fail,
no recorded SHA, no PR number, no resolvable project, git/closed-PR lookup
returns ``None``) — only the exact-unchanged case is hard-blocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy import lifecycle as spec_module
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from structlog.testing import capture_logs

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


def _resubmit_root(
    *,
    notes_structured: dict[str, Any] | None,
    pr_number: int | None = 139,
) -> tuple[Choreographer, Any, Any]:
    """A Main-PM root re-submitted from ``in_progress`` after a ``pr_fail``.

    Mirrors the live c80e19ff / PR #139 re-submit: the root is back in
    ``in_progress`` (re-claimed out of ``needs_revision``), carries the prior
    ``pr_fail`` verdict in ``notes_structured.pr_review``, and the PR is still
    open. The ``_submit_up_guard`` preflight is satisfied (owned, journal
    decision, subtasks terminal, branch present, notes long enough) so the
    unchanged-PR gate is the thing under test.
    """
    main_pm_id = uuid4()
    root_task_id = uuid4()
    in_prog = MagicMock(
        id=root_task_id,
        status="in_progress",
        assigned_to=main_pm_id,
        pr_number=pr_number,
        branch_name="feature/main_pm/c80e19ff",
        parent_task_id=None,
        batch_id=None,
        team="main_pm",
        notes_structured=notes_structured,
    )
    gated = MagicMock(**{**in_prog.__dict__, "status": "awaiting_pr_review"})
    task_svc = AsyncMock()
    task_svc.get.return_value = in_prog
    task_svc.submit_for_review.return_value = gated
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.uncovered_parent_acceptance_criteria.return_value = []
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )
    c = Choreographer(_make_deps(task=task_svc, git=AsyncMock()))
    # Real _project_slug_for would walk a mock session into a MagicMock slug; the
    # gate under test needs a real string slug + a controllable head SHA. Alias to
    # ``Any`` so mypy doesn't flag the method-spy assignment (no type:ignore owed).
    cc: Any = c
    cc._project_slug_for = AsyncMock(return_value="proj-slug")
    return c, main_pm_id, root_task_id


# ---------------------------------------------------------------------------
# The hard block — refuse the byte-identical re-submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_root_refuses_unchanged_pr_after_pr_fail() -> None:
    """The loop-stopper still holds past the one-shot exemption: prior
    pr_fail stamped head SHA X, the PR head is still X (no new cell work on
    the root branch). The findings ledger here fail-opens to "nothing open"
    (mock session, no real query) — the same signal ``_check_submit_up_gates``
    upstream already reads for FINDINGS_ADDRESSED — so the first resubmit at
    this head is the one-shot exemption (see
    test_resubmit_unchanged_head_exemption.py for full exemption coverage);
    a second resubmit at the SAME head refuses, so the loop still can't run
    forever."""
    c, main_pm_id, root_task_id = _resubmit_root(
        notes_structured={
            "pr_review": {"verdict": "failed", "head_sha": SHA_OLD, "summary": "..."}
        }
    )
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_OLD)

    first = await c.submit_root(
        main_pm_id, root_task_id, notes="re-submitting the root after the fix"
    )
    assert first.error is None, first.as_dict()

    env = await c.submit_root(
        main_pm_id, root_task_id, notes="re-submitting again; still unchanged"
    )

    assert env.error is not None, env.as_dict()
    assert env.error == "invalid_state"
    assert "unchanged" in (env.message or "").lower()
    remediate = env.remediate or ""
    assert "re-delegate" in remediate
    assert "submit_root" in remediate
    # The second attempt's PR was NOT re-opened / re-pushed — the runner ran
    # only for the first (exempted) call.
    c.task.submit_for_review.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_root_allows_after_root_branch_advanced() -> None:
    """A different current head SHA ⇒ cell work landed on the root branch ⇒
    the diff changed ⇒ allow the re-submit into the gate."""
    c, main_pm_id, root_task_id = _resubmit_root(
        notes_structured={
            "pr_review": {"verdict": "failed", "head_sha": SHA_OLD, "summary": "..."}
        }
    )
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_NEW)

    env = await c.submit_root(
        main_pm_id, root_task_id, notes="re-submitting after the cell re-assembly"
    )

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"
    c.task.submit_for_review.assert_awaited_once()


# ---------------------------------------------------------------------------
# Fail-open — ambiguous cases proceed and rely on the reviewer to re-fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_root_fail_open_when_no_prior_pr_fail_verdict() -> None:
    """No pr_review (first submit) or a passed verdict ⇒ nothing to compare ⇒
    allow."""
    c, main_pm_id, root_task_id = _resubmit_root(notes_structured=None)
    env = await c.submit_root(
        main_pm_id, root_task_id, notes="first root submit; nothing to compare yet"
    )
    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"


@pytest.mark.asyncio
async def test_submit_root_fail_open_when_prior_fail_recorded_no_head_sha() -> None:
    """A pr_fail verdict written before this field existed has no ``head_sha`` ⇒
    cannot compare ⇒ allow (fail open, not wedge)."""
    c, main_pm_id, root_task_id = _resubmit_root(
        notes_structured={"pr_review": {"verdict": "failed", "summary": "..."}}
    )
    env = await c.submit_root(
        main_pm_id, root_task_id, notes="re-submit; no recorded sha to compare"
    )
    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"


@pytest.mark.asyncio
async def test_submit_root_fail_open_when_git_lookup_returns_none() -> None:
    """A closed/missing PR or a git error returns ``None`` ⇒ ambiguous ⇒ allow
    (the reviewer can still pr_fail if the diff is bad)."""
    c, main_pm_id, root_task_id = _resubmit_root(
        notes_structured={
            "pr_review": {"verdict": "failed", "head_sha": SHA_OLD, "summary": "..."}
        }
    )
    c.git.get_pr_head_sha = AsyncMock(return_value=None)
    env = await c.submit_root(
        main_pm_id, root_task_id, notes="re-submit; the prior PR was closed or missing"
    )
    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"


@pytest.mark.asyncio
async def test_submit_root_fail_open_when_no_pr_number() -> None:
    """A root with no ``pr_number`` has nothing to look up ⇒ allow."""
    c, main_pm_id, root_task_id = _resubmit_root(
        notes_structured={
            "pr_review": {"verdict": "failed", "head_sha": SHA_OLD, "summary": "..."}
        },
        pr_number=None,
    )
    env = await c.submit_root(
        main_pm_id, root_task_id, notes="re-submit; this root has no pr number"
    )
    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"


@pytest.mark.asyncio
async def test_submit_root_fail_open_when_slug_unresolvable() -> None:
    """No resolvable project slug (a product-only root the product service can't
    expand) ⇒ can't query git ⇒ allow."""
    c, main_pm_id, root_task_id = _resubmit_root(
        notes_structured={
            "pr_review": {"verdict": "failed", "head_sha": SHA_OLD, "summary": "..."}
        }
    )
    cc: Any = c
    cc._project_slug_for = AsyncMock(return_value=None)
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_OLD)
    env = await c.submit_root(
        main_pm_id, root_task_id, notes="re-submit; project slug unresolvable here"
    )
    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"


@pytest.mark.asyncio
async def test_submit_root_fail_open_when_git_lookup_raises() -> None:
    """A git lookup that raises must not 500 the PM — the gate swallows it and
    proceeds (fail open)."""
    c, main_pm_id, root_task_id = _resubmit_root(
        notes_structured={
            "pr_review": {"verdict": "failed", "head_sha": SHA_OLD, "summary": "..."}
        }
    )
    c.git.get_pr_head_sha = AsyncMock(side_effect=RuntimeError("boom"))
    env = await c.submit_root(
        main_pm_id, root_task_id, notes="re-submit; git head-sha lookup raised an error"
    )
    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"


@pytest.mark.asyncio
async def test_submit_root_fail_open_logs_when_slug_resolver_raises() -> None:
    """#5: a regression in ``_project_slug_for`` (or ``get_pr_head_sha``) used to
    make the loop-stopper a SILENT no-op — the broad ``except Exception`` in
    ``_current_pr_head_sha`` swallowed it with no log, re-opening the
    pr_fail re-submit loop invisibly. The gate still fails open (do NOT
    invert to fail-closed — that would wedge the PM), but the swallow must
    now emit a warning so a resolver regression is visible to the operator."""
    c, main_pm_id, root_task_id = _resubmit_root(
        notes_structured={
            "pr_review": {"verdict": "failed", "head_sha": SHA_OLD, "summary": "..."}
        }
    )
    cc: Any = c
    cc._project_slug_for = AsyncMock(side_effect=RuntimeError("resolver regression"))

    with capture_logs() as logs:
        env = await c.submit_root(
            main_pm_id, root_task_id, notes="re-submit; slug resolver blew up"
        )

    # Still fail-open — never wedge the PM on a lookup error.
    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"
    # ...but the swallow is no longer silent.
    assert any(
        entry["log_level"] == "warning"
        and "head_sha" in entry["event"]
        and "resolver regression" in str(entry.get("error", ""))
        for entry in logs
    ), [e.get("event") for e in logs if e["log_level"] == "warning"]


# ---------------------------------------------------------------------------
# The capture side — pr_fail stamps the head SHA into notes_structured
# ---------------------------------------------------------------------------


def _make_choreographer_for_gate() -> Choreographer:
    """A choreographer wired to drive ``_gate_decision`` past preflight/tracing
    and into the verdict-record step without exercising the heavy ownership
    logic (those have their own tests). Mirrors test_pr_gate_notifies_pm."""
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    # pr_fail inserts its findings into the ledger before the transition;
    # the repository needs an awaitable ``flush()`` on the mock session.
    base["task"].session = MagicMock()
    base["task"].session.add = MagicMock()
    base["task"].session.flush = AsyncMock()
    # pr_pass's verified-stamp (ReviewFindingsRepository.list_for_task) reads
    # via session.execute — an empty scalars result (no findings).
    base["task"].session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    return Choreographer(ChoreographerDeps(**base))


def _stub_gate_path(
    c: Choreographer, *, reviewer_id: Any, t_before: Any, t_after: Any
) -> MagicMock:
    """Drive ``_gate_decision`` past preflight/tracing/post and into the verdict
    record step. Returns the ``_record_gate_verdict`` spy so callers can assert
    on the recorded kwargs. The ``cc: Any`` alias is the method-spy idiom: mypy
    doesn't flag attribute assignment on ``Any`` (no method-assign / no
    attr-defined), so no ``type: ignore`` is owed and ruff's B010 (no ``setattr``
    with a constant) is sidestepped too.
    """
    cc: Any = c
    agent = MagicMock(role="pr_reviewer", slug="be-pr-reviewer")
    cc._gate_preflight = AsyncMock(
        return_value=(
            t_before,
            agent,
            "pr_reviewer",
            {},
            spec_module.Context(actor_id=reviewer_id),
        )
    )
    cc._gate_tracing = AsyncMock(return_value=None)
    # Spy on the verdict record so we can assert the head_sha kwarg without
    # running the real apply_structured_note (which needs a real ORM task).
    record_spy = MagicMock()
    cc._record_gate_verdict = record_spy
    cc._post_gate_review_to_pr = AsyncMock()
    runner = MagicMock()
    runner.run_intent = AsyncMock(return_value=t_after)
    cc._verb_runner = MagicMock(return_value=runner)
    return record_spy


@pytest.mark.asyncio
async def test_pr_fail_captures_head_sha_into_verdict() -> None:
    """pr_fail resolves the PR's head SHA and threads it into the verdict record
    so the next submit_root can compare against it."""
    reviewer_id = uuid4()
    pm_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=139,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )
    t_after = MagicMock(
        id=task_id,
        assigned_to=pm_id,
        pr_number=139,
        parent_task_id=uuid4(),
        status="needs_revision",
    )

    c = _make_choreographer_for_gate()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after
    )
    cc: Any = c
    cc._project_slug_for = AsyncMock(return_value="proj-slug")
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_OLD)

    await c.pr_fail(reviewer_id, task_id, ["duplicate TimeseriesChart export"])

    record_spy.assert_called_once()
    kwargs = record_spy.call_args.kwargs
    assert kwargs["head_sha"] == SHA_OLD


@pytest.mark.asyncio
async def test_pr_fail_capture_best_effort_when_git_raises() -> None:
    """A git head-sha lookup that raises must not crash the gate — head_sha
    falls back to None (the submit_root gate then fails open) and the
    transition still proceeds to needs_revision."""
    reviewer_id = uuid4()
    pm_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=139,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )
    t_after = MagicMock(
        id=task_id, assigned_to=pm_id, pr_number=139, status="needs_revision"
    )

    c = _make_choreographer_for_gate()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after
    )
    cc: Any = c
    cc._project_slug_for = AsyncMock(return_value="proj-slug")
    c.git.get_pr_head_sha = AsyncMock(side_effect=RuntimeError("github 503"))

    env = await c.pr_fail(reviewer_id, task_id, ["a concrete issue"])

    assert env.status == "needs_revision"
    record_spy.assert_called_once()
    assert record_spy.call_args.kwargs["head_sha"] is None


@pytest.mark.asyncio
async def test_pr_fail_re_captures_head_sha_after_transition_commits() -> None:
    """#189: ``_record_gate_verdict_for`` captures the PR head SHA BEFORE
    ``run_intent`` commits the transition (so the verdict note rides the same
    commit). If the assembled PR advances between that capture and the commit,
    the recorded SHA is stale vs the PR head at the moment of needs_revision —
    and the ``submit_root`` loop-stopper then false-ALLOWS an unchanged
    re-submit (current head vs an older recorded head ⇒ "different" ⇒ allow),
    re-opening the pr_fail loop. The fix re-captures the SHA AFTER the
    transition commits and re-stamps the note when it changed, so the recorded
    SHA is the PR head at the moment of needs_revision. Here the PR advances
    from SHA_OLD (pre-transition capture) to SHA_NEW (post-transition
    re-capture): the final recorded head_sha is SHA_NEW."""
    reviewer_id = uuid4()
    pm_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=139,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )
    t_after = MagicMock(
        id=task_id, assigned_to=pm_id, pr_number=139, status="needs_revision"
    )

    c = _make_choreographer_for_gate()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after
    )
    cc: Any = c
    cc._project_slug_for = AsyncMock(return_value="proj-slug")
    # Pre-transition capture -> SHA_OLD; post-transition re-capture -> SHA_NEW
    # (cell work landed on the root branch mid-gate).
    c.git.get_pr_head_sha = AsyncMock(side_effect=[SHA_OLD, SHA_NEW])

    env = await c.pr_fail(reviewer_id, task_id, ["a concrete issue"])

    assert env.status == "needs_revision"
    # The pre-transition call wrote SHA_OLD; the post-transition re-stamp wrote
    # SHA_NEW (the authoritative head at needs_revision time). The ordered
    # sequence pins both calls in order (a list compare, not a magic count).
    shas = [c.kwargs["head_sha"] for c in record_spy.call_args_list]
    assert shas == [SHA_OLD, SHA_NEW]


@pytest.mark.asyncio
async def test_pr_fail_skips_re_stamp_when_head_sha_unchanged() -> None:
    """#189 companion: when the PR head did NOT advance between the pre-transition
    capture and the commit, the re-capture matches and the gate does NOT re-stamp
    (no second note write) — the pre-transition SHA already records the truth.
    This keeps the no-advance case to a single ``_record_gate_verdict`` call and
    a single GitHub head-sha lookup pair (no extra write when nothing moved)."""
    reviewer_id = uuid4()
    pm_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=139,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )
    t_after = MagicMock(
        id=task_id, assigned_to=pm_id, pr_number=139, status="needs_revision"
    )

    c = _make_choreographer_for_gate()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after
    )
    cc: Any = c
    cc._project_slug_for = AsyncMock(return_value="proj-slug")
    # PR head stable across the gate.
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_OLD)

    env = await c.pr_fail(reviewer_id, task_id, ["a concrete issue"])

    assert env.status == "needs_revision"
    # No advance -> single pre-transition write, no re-stamp.
    record_spy.assert_called_once()
    assert record_spy.call_args.kwargs["head_sha"] == SHA_OLD


@pytest.mark.asyncio
async def test_pr_pass_does_not_capture_head_sha() -> None:
    """Only pr_fail stamps a head SHA — pr_pass must not (there is no loop to
    guard against a pass)."""
    reviewer_id = uuid4()
    pm_id = uuid4()
    task_id = uuid4()
    t_before = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        pr_number=42,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
    )
    t_after = MagicMock(
        id=task_id, assigned_to=pm_id, pr_number=42, status="awaiting_pm_review"
    )

    c = _make_choreographer_for_gate()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after
    )
    cc: Any = c
    cc._project_slug_for = AsyncMock(return_value="proj-slug")
    c.git.get_pr_head_sha = AsyncMock(return_value=SHA_OLD)

    await c.pr_pass(reviewer_id, task_id, "Assembled root scope is clean.")

    record_spy.assert_called_once()
    # pr_pass path never calls _capture_pr_head_sha, so head_sha is absent
    # from the kwargs (the default None is not passed).
    assert "head_sha" not in record_spy.call_args.kwargs


# ---------------------------------------------------------------------------
# submit_root must not 500 when submit_for_review returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_root_invalid_state_when_submit_for_review_returns_none() -> None:
    """submit_for_review returns None when the root->master PR was already
    opened (task raced out of in_progress, or a prior call transitioned it).
    submit_root must surface ``invalid_state``, not dereference None.status
    and 500."""
    c, main_pm_id, root_task_id = _resubmit_root(notes_structured=None)
    # The transition did not happen (PR already opened / task raced).
    c.task.submit_for_review.return_value = None

    env = await c.submit_root(
        main_pm_id, root_task_id, notes="re-submit; transition returned nothing"
    )

    assert env.error is not None, env.as_dict()
    assert env.error == "invalid_state"
    remediate = env.remediate or ""
    assert "evidence" in remediate.lower() or "re-fetch" in remediate.lower()
