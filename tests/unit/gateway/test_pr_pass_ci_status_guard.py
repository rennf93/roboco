"""pr_pass refuses to pass an assembled PR unless CI on its head commit is green.

Before this guard, ``pr_pass`` had no CI-status check at all — a reviewer could
pass an assembled PR whose CI was red, still running, or not yet scheduled.
``_ci_status_guard`` (wired into ``_pr_pass_blocked`` alongside the existing
toolchain/conventions guards) reads ``GitService.get_pr_ci_status`` and blocks
on failure/pending/pending_not_scheduled/error; only ``failure`` remediates
via ``pr_fail`` (never ``i_am_blocked`` — a reviewer has no such verb) — the
``error`` state is a transient GitHub API lookup failure and remediates via
retry only, never ``pr_fail``. A project with no CI configured at all passes
through cleanly, stamping the verdict note with why the guard did not block.
``pr_fail`` is unaffected by CI state entirely, and the separate inbound
``PRReviewerMixin`` surface (``claim_pr_review`` / ``post_pr_review``) never
consults CI status at all.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy.content import Finding, Severity
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
    pr_review,
)


def _make_choreographer() -> Choreographer:
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
    # The drift guard wraps the findings insertion in a savepoint
    # (``async with self.task.session.begin_nested()``). Without this mock
    # the ``async with`` silently fails and ``insert_and_render`` is never
    # called — the test would verify the rejection envelope but not AC6
    # (the findings-ledger write). Matches the pattern in
    # test_choreographer_completion_guards.py:43 and test_verb_runner.py:89.
    base["task"].session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return Choreographer(ChoreographerDeps(**base))


def _stub_gate_path(
    c: Choreographer, *, reviewer_id: Any, t_before: Any, t_after: Any
) -> MagicMock:
    """Drive ``_gate_decision`` past preflight/tracing and into the real
    ``_pr_pass_blocked`` -> ``_ci_status_guard`` path — only the ownership/
    tracing plumbing is stubbed (it has its own tests); the CI guard under
    test runs for real. Mirrors ``test_pr_gate_notifies_pm._stub_gate_path``.
    """
    agent = MagicMock(role="pr_reviewer", slug="be-pr-reviewer")
    cc: Any = c
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
    cc._project_slug_for = AsyncMock(return_value="proj-slug")
    record_spy = MagicMock()
    cc._record_gate_verdict = record_spy
    cc._post_gate_review_to_pr = AsyncMock()
    runner = MagicMock()
    runner.run_intent = AsyncMock(return_value=t_after)
    cc._verb_runner = MagicMock(return_value=runner)
    return record_spy


def _t(
    *,
    status: str = "awaiting_pr_review",
    pr_number: int | None = 42,
    intends_to_touch: list[str] | None = None,
) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        assigned_to=None,
        pr_number=pr_number,
        parent_task_id=uuid4(),
        status=status,
        intends_to_touch=intends_to_touch,
    )


# ---------------------------------------------------------------------------
# The six CI-guard branches, exercised through pr_pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_pass_blocked_on_failing_ci() -> None:
    reviewer_id = uuid4()
    t_before = _t()
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(
        return_value={"state": "failure", "failing_checks": ["tests"]}
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "CI is failing" in (env.message or "")
    assert "tests" in (env.message or "")
    assert "pr_fail" in (env.remediate or "")
    c.task.get.assert_not_called()  # never reached the runner
    cc: Any = c
    cc._record_gate_verdict.assert_not_called()


@pytest.mark.asyncio
async def test_pr_pass_scope_gates_out_of_scope_codeql_failure() -> None:
    """A failing CodeQL check for a language the task never declared it
    touches (``intends_to_touch`` scope has zero overlap with that check's
    analyzed paths) must not block — CodeQL scans the whole language, not
    the diff, so this is a pre-existing finding outside the task's scope."""
    reviewer_id = uuid4()
    t_before = _t(intends_to_touch=["panel/**"])
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after
    )
    c.git.get_pr_ci_status = AsyncMock(
        return_value={"state": "failure", "failing_checks": ["Analyze (python)"]}
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"
    record_spy.assert_called_once()
    ci_note = record_spy.call_args.kwargs.get("ci_note") or ""
    assert "outside declared scope" in ci_note
    assert "Analyze (python)" in ci_note


@pytest.mark.asyncio
async def test_pr_pass_still_blocked_on_in_scope_codeql_failure() -> None:
    """A failing CodeQL check whose declared scope DOES overlap the task's
    intends_to_touch still blocks — the scope gate only excuses genuinely
    out-of-bounds findings."""
    reviewer_id = uuid4()
    t_before = _t(intends_to_touch=["roboco/services/foo.py"])
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(
        return_value={"state": "failure", "failing_checks": ["Analyze (python)"]}
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "Analyze (python)" in (env.message or "")


@pytest.mark.asyncio
async def test_pr_pass_scope_gate_narrows_message_to_remaining_failures() -> None:
    """A mix of an out-of-scope CodeQL check and a genuinely failing,
    non-CodeQL check: the scope-gated check is dropped from the block
    message, but the remaining failure still blocks."""
    reviewer_id = uuid4()
    t_before = _t(intends_to_touch=["panel/**"])
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(
        return_value={
            "state": "failure",
            "failing_checks": ["Analyze (python)", "tests"],
        }
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "tests" in (env.message or "")
    assert "Analyze (python)" not in (env.message or "")


# ---------------------------------------------------------------------------
# Scope-relaxation drift guard: verifies the actual diff against the declared
# scope when a CodeQL relaxation was claimed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_relaxation_passes_when_files_in_declared_scope() -> None:
    """AC: A task whose changed files fall entirely inside its declared
    intends_to_touch globs passes the gate unchanged after a CodeQL scope
    relaxation."""
    reviewer_id = uuid4()
    t_before = _t(intends_to_touch=["panel/**"])
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after
    )
    c.git.get_pr_ci_status = AsyncMock(
        return_value={"state": "failure", "failing_checks": ["Analyze (python)"]}
    )
    c.git.list_changed_files = AsyncMock(
        return_value=["panel/src/foo.tsx", "panel/src/bar.tsx"]
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"
    record_spy.assert_called_once()
    ci_note = record_spy.call_args.kwargs.get("ci_note") or ""
    assert "outside declared scope" in ci_note


@pytest.mark.asyncio
async def test_scope_relaxation_blocks_when_files_outside_declared_scope() -> None:
    """AC: A task that declared a narrow scope, touched files outside it, and
    received a CodeQL scope relaxation is blocked at pr_pass with an
    invalid_state envelope naming each out-of-scope file."""
    reviewer_id = uuid4()
    t_before = _t(intends_to_touch=["panel/**"])
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(
        return_value={"state": "failure", "failing_checks": ["Analyze (python)"]}
    )
    c.git.list_changed_files = AsyncMock(
        return_value=["panel/src/foo.tsx", "roboco/services/hack.py"]
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "roboco/services/hack.py" in (env.message or "")
    assert "outside" in (env.message or "").lower()
    # The in-scope file must NOT be named as a drift file.
    assert "panel/src/foo.tsx" not in (env.message or "")


@pytest.mark.asyncio
async def test_scope_relaxation_drift_writes_findings_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6: when the drift guard detects an out-of-scope file, it records
    each drifted file as a findings-ledger row via
    ``findings_lib.insert_and_render`` (origin=pr_gate) before returning the
    rejection envelope. Without the ``begin_nested`` mock in
    ``_make_choreographer`` the savepoint silently fails and the ledger
    write is skipped — this test catches that regression."""
    insert_mock = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.gateway.choreographer.pr_gate.findings_lib.insert_and_render",
        insert_mock,
    )
    reviewer_id = uuid4()
    t_before = _t(intends_to_touch=["panel/**"])
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(
        return_value={"state": "failure", "failing_checks": ["Analyze (python)"]}
    )
    c.git.list_changed_files = AsyncMock(
        return_value=["panel/src/foo.tsx", "roboco/services/hack.py"]
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    # The rejection envelope still fires.
    assert env.error == "invalid_state"
    assert "roboco/services/hack.py" in (env.message or "")
    # The findings-ledger write was attempted exactly once.
    insert_mock.assert_awaited_once()
    call = insert_mock.call_args
    assert call.kwargs.get("origin") == "pr_gate"
    findings: list[Finding] = call.kwargs.get("findings") or []
    drifted = [f for f in findings if f.file == "roboco/services/hack.py"]
    assert len(drifted) == 1
    assert drifted[0].severity == Severity.MAJOR
    assert "CodeQL scope relaxation" in (drifted[0].evidence or "")
    # The in-scope file must not appear as a finding.
    assert not any(f.file == "panel/src/foo.tsx" for f in findings)


@pytest.mark.asyncio
async def test_scope_relaxation_drift_checked_without_colliding_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC: Drift is evaluated for a relaxation-claiming task even with no
    colliding sibling — the check is unconditional, calling _drift directly
    rather than through build_collision_context. Monkeypatching
    build_collision_context to return None (its no-sibling behaviour) must NOT
    suppress the drift block."""
    reviewer_id = uuid4()
    t_before = _t(intends_to_touch=["panel/**"])
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(
        return_value={"state": "failure", "failing_checks": ["Analyze (python)"]}
    )
    c.git.list_changed_files = AsyncMock(return_value=["roboco/services/hack.py"])
    monkeypatch.setattr(
        "roboco.services.gateway.choreographer.pr_gate.build_collision_context",
        lambda **_kw: None,
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "roboco/services/hack.py" in (env.message or "")


@pytest.mark.asyncio
async def test_no_declared_scope_no_relaxation_no_drift_finding() -> None:
    """AC: A task with no declared intends_to_touch behaves exactly as today —
    no CodeQL relaxation granted and no drift finding recorded. The failing
    CodeQL check still blocks (conservative), and the drift guard is never
    reached (list_changed_files is never called)."""
    reviewer_id = uuid4()
    t_before = _t(intends_to_touch=None)
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(
        return_value={"state": "failure", "failing_checks": ["Analyze (python)"]}
    )
    c.git.list_changed_files = AsyncMock(return_value=["roboco/services/hack.py"])

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    # No relaxation — the standard CI failure block fires.
    assert env.error == "invalid_state"
    assert "Analyze (python)" in (env.message or "")
    # The drift guard was never called (no relaxation → no drift check).
    assert "outside declared scope" not in (env.message or "")
    c.git.list_changed_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_pass_still_blocked_on_unmapped_codeql_check_name() -> None:
    """A failing check name that LOOKS like CodeQL but isn't in
    ``_CODEQL_CHECK_SCOPES`` (a renamed workflow, or a future third
    language/matrix entry) must still block — the scope gate only excuses a
    check it can actually map to an analyzed-language scope; an unmapped name
    falls back to the old conservative (blocking) behavior rather than being
    silently excused."""
    reviewer_id = uuid4()
    t_before = _t(intends_to_touch=["panel/**"])
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(
        return_value={"state": "failure", "failing_checks": ["CodeQL"]}
    )

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "CodeQL" in (env.message or "")


@pytest.mark.asyncio
async def test_pr_pass_blocked_on_pending_ci() -> None:
    reviewer_id = uuid4()
    t_before = _t()
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(return_value={"state": "pending"})

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "still running" in (env.message or "")
    assert "wait" in (env.remediate or "").lower()


@pytest.mark.asyncio
async def test_pr_pass_blocked_on_zero_checks_workflows_pending() -> None:
    reviewer_id = uuid4()
    t_before = _t()
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(return_value={"state": "pending_not_scheduled"})

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "has not started" in (env.message or "")


@pytest.mark.asyncio
async def test_pr_pass_blocked_on_github_api_error() -> None:
    reviewer_id = uuid4()
    t_before = _t()
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=None)
    c.git.get_pr_ci_status = AsyncMock(return_value={"state": "error"})

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error == "invalid_state"
    assert "GitHub API error" in (env.message or "")
    assert "retry" in (env.remediate or "").lower()
    assert "do NOT pr_fail" in (env.remediate or "")


@pytest.mark.asyncio
async def test_pr_pass_succeeds_on_all_green() -> None:
    reviewer_id = uuid4()
    t_before = _t()
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after
    )
    c.git.get_pr_ci_status = AsyncMock(return_value={"state": "success"})

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"
    record_spy.assert_called_once()
    # No CI note stamped when the guard passed because CI was actually green.
    assert record_spy.call_args.kwargs.get("ci_note") is None


@pytest.mark.asyncio
async def test_pr_pass_passes_through_when_no_ci_configured_and_stamps_evidence() -> (
    None
):
    reviewer_id = uuid4()
    t_before = _t()
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    record_spy = _stub_gate_path(
        c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after
    )
    c.git.get_pr_ci_status = AsyncMock(return_value={"state": "no_ci_configured"})

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"
    record_spy.assert_called_once()
    assert (
        record_spy.call_args.kwargs.get("ci_note") == "no CI configured on this project"
    )


@pytest.mark.asyncio
async def test_pr_pass_fails_open_when_ci_status_unresolvable() -> None:
    """A configuration gap (no resolvable project/token/head sha) -> None from
    get_pr_ci_status -> the guard never blocks (fail open, matches the other
    pr_pass guards' posture on an unresolvable signal)."""
    reviewer_id = uuid4()
    t_before = _t()
    t_after = _t(status="awaiting_pm_review")
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    c.git.get_pr_ci_status = AsyncMock(return_value=None)

    env = await c.pr_pass(reviewer_id, t_before.id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"


# ---------------------------------------------------------------------------
# Regression: pr_fail is unaffected by CI state entirely
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_fail_succeeds_regardless_of_ci_state() -> None:
    """pr_fail must never consult get_pr_ci_status — the CI guard lives only in
    the pr_pass branch of _gate_decision."""
    reviewer_id = uuid4()
    t_before = _t()
    t_after = _t(status="needs_revision")
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    # Even if configured to report red CI, pr_fail must not care — and must not
    # even call it.
    c.git.get_pr_ci_status = AsyncMock(return_value={"state": "failure"})

    env = await c.pr_fail(reviewer_id, t_before.id, ["a concrete actionable issue"])

    assert env.error is None, env.as_dict()
    assert env.status == "needs_revision"
    c.git.get_pr_ci_status.assert_not_awaited()


# ---------------------------------------------------------------------------
# Regression: the inbound PRReviewerMixin surface never consults CI status
# ---------------------------------------------------------------------------


def test_pr_review_mixin_has_no_ci_status_coupling() -> None:
    """claim_pr_review / post_pr_review (the external inbound review surface,
    ``PRReviewerMixin`` in pr_review.py) must stay completely untouched by the
    new CI-status guard — it is wired only into ``PRGateMixin.pr_pass``
    (the in-path assembled-PR gate) via ``_pr_pass_blocked``. A source-level
    check is the most robust regression here: any accidental import or call of
    ``get_pr_ci_status`` / ``_ci_status_guard`` into the inbound mixin fails
    this immediately, regardless of how its heavier claim/decision plumbing
    (self_review_block, tracing, content gates) evolves."""
    source = inspect.getsource(pr_review)
    assert "get_pr_ci_status" not in source
    assert "_ci_status_guard" not in source
    assert not hasattr(pr_review.PRReviewerMixin, "_ci_status_guard")


# ---------------------------------------------------------------------------
# Regression-lock: this task's 7 ACs mapped to the test(s) that cover each
# ---------------------------------------------------------------------------


def test_ac_coverage_map_and_pr_reviewer_prompt_states_ci_guard() -> None:
    """Explicit AC-to-test mapping for this task's 7 acceptance criteria.

    AC1 (CI failure names the failing check) ->
        test_pr_pass_blocked_on_failing_ci (this file, line ~90)
    AC2 (pending vs error are distinct invalid_state envelopes with a
        different remediate text) -> test_pr_pass_blocked_on_pending_ci
        (~111), test_pr_pass_blocked_on_github_api_error (~140)
    AC3 (pending_not_scheduled is the retryable not-yet-scheduled case) ->
        test_pr_pass_blocked_on_zero_checks_workflows_pending (~126)
    AC4 (no_ci_configured passes through + stamps the ci_status verdict
        note) -> test_pr_pass_passes_through_when_no_ci_configured_and_
        stamps_evidence (~175)
    AC5 (all-green CI passes through with no note) ->
        test_pr_pass_succeeds_on_all_green (~155)
    AC6 (pr_fail and the inbound PRReviewerMixin have zero CI-status
        coupling) -> test_pr_fail_succeeds_regardless_of_ci_state (~221),
        test_pr_review_mixin_has_no_ci_status_coupling (~245)
    AC7 (the pr_reviewer prompt states the per-AC evidence-walk + CI-status
        guard section) -> asserted directly below. Previously verified only
        by manual reading during self-verification, with no test-level
        regression lock — this closes that gap.
    """
    prompt_path = (
        Path(__file__).resolve().parents[3]
        / "agents"
        / "prompts"
        / "roles"
        / "pr_reviewer.md"
    )
    text = prompt_path.read_text(encoding="utf-8")
    assert "per-AC evidence-walk" in text
    assert "named-deliverable/silent-drop rule" in text
    assert "CI status:" in text
    assert 'ci_status: "no CI configured on this project"' in text
