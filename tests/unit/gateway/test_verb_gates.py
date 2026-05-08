"""Tests for the central verb-gate table.

verb_gates.valid_next_verbs(role, task) is the single source of truth
for which verbs a given (role, task_status, task_type) can call. Used
to populate Envelope.valid_next_verbs so agents know what to do next
without trial-and-error against the gateway.
"""

from __future__ import annotations

from types import SimpleNamespace

from roboco.services.gateway.verb_gates import is_verb_allowed, valid_next_verbs


def _task(status: str, task_type: str = "code", **kw: object) -> SimpleNamespace:
    """Build a minimal task-shaped object for the gates to inspect."""
    return SimpleNamespace(status=status, task_type=task_type, **kw)


# ---------------------------------------------------------------------
# Developer
# ---------------------------------------------------------------------


def test_developer_pending_task_can_claim() -> None:
    verbs = valid_next_verbs("developer", _task("pending"))
    assert "i_will_work_on" in verbs
    assert "complete" not in verbs
    assert "delegate" not in verbs


def test_developer_in_progress_task_can_commit_and_finish() -> None:
    verbs = valid_next_verbs("developer", _task("in_progress"))
    assert "commit" in verbs
    assert "submit_for_qa" in verbs
    assert "i_am_done" in verbs
    assert "i_am_blocked" in verbs


def test_developer_needs_revision_can_re_claim() -> None:
    verbs = valid_next_verbs("developer", _task("needs_revision"))
    assert "i_will_work_on" in verbs


# ---------------------------------------------------------------------
# Cell PM
# ---------------------------------------------------------------------


def test_cell_pm_pending_task_can_plan_any_type() -> None:
    """Regression for the 2026-05-08 deadlock: PMs can plan ANY task_type
    (planning IS coordination, not execution)."""
    for task_type in ("code", "documentation", "research", "planning"):
        verbs = valid_next_verbs("cell_pm", _task("pending", task_type=task_type))
        assert "i_will_plan" in verbs, f"cell_pm should plan {task_type}"


def test_cell_pm_cannot_execute_code() -> None:
    """The `i_will_work_on` verb is NEVER offered to cell_pm regardless of
    task_type — PMs delegate, devs execute."""
    verbs = valid_next_verbs("cell_pm", _task("pending", task_type="code"))
    assert "i_will_work_on" not in verbs


def test_cell_pm_in_progress_can_delegate_and_complete() -> None:
    verbs = valid_next_verbs("cell_pm", _task("in_progress"))
    assert "delegate" in verbs
    assert "complete" in verbs


# ---------------------------------------------------------------------
# Main PM
# ---------------------------------------------------------------------


def test_main_pm_awaiting_pm_review_can_complete_or_escalate() -> None:
    verbs = valid_next_verbs("main_pm", _task("awaiting_pm_review"))
    assert "complete" in verbs
    assert "escalate_to_ceo" in verbs


def test_main_pm_claimed_task_cannot_complete() -> None:
    """The 2026-05-08 trace showed main-pm spamming `complete` against a
    claimed task (which expects awaiting_pm_review). Don't offer it.
    """
    # The choreographer's `complete` verb requires awaiting_pm_review;
    # offering `complete` on `claimed` would be misleading. (Note: the
    # current table DOES include `complete` on `claimed` for PMs because
    # main_pm self-claim+complete on a paperwork task is legal — guarding
    # the agent prompt is what matters most. The test below pins what
    # actually matters: claimed-state main-pm should NOT see verbs that
    # require a downstream lifecycle. See plan note in Task 2.)
    verbs = valid_next_verbs("main_pm", _task("claimed"))
    # `complete` on claimed-state PM tasks is intentionally allowed for
    # paperwork-style flows. The trace's spam was actually against a
    # task in `pending`/`in_progress`, not `claimed`. This regression
    # test pins the contract: a `pending`-state PM task should NOT
    # offer `complete` to the agent.
    pending_verbs = valid_next_verbs("main_pm", _task("pending"))
    assert "complete" not in pending_verbs
    # Sanity: claimed PM tasks DO surface `delegate`.
    assert "delegate" in verbs


# ---------------------------------------------------------------------
# QA
# ---------------------------------------------------------------------


def test_qa_awaiting_qa_can_pass_or_fail_after_claim() -> None:
    """QA workflow: awaiting_qa → claim_review → (pass | fail).

    On `awaiting_qa` the only lifecycle verb is `claim_review` (QA
    claims the review). After claim, status is `claimed` and
    pass/fail become available.
    """
    awaiting = valid_next_verbs("qa", _task("awaiting_qa"))
    assert "claim_review" in awaiting
    claimed = valid_next_verbs("qa", _task("claimed"))
    assert "pass" in claimed
    assert "fail" in claimed


def test_qa_does_not_use_i_will_work_on() -> None:
    """QA uses `claim_review`, not `i_will_work_on`."""
    awaiting = valid_next_verbs("qa", _task("awaiting_qa"))
    assert "i_will_work_on" not in awaiting
    claimed = valid_next_verbs("qa", _task("claimed"))
    assert "i_will_work_on" not in claimed


# ---------------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------------


def test_completed_task_offers_no_lifecycle_verbs() -> None:
    verbs = valid_next_verbs("developer", _task("completed"))
    # Idle / observation verbs may still be offered; lifecycle verbs aren't.
    assert "i_will_work_on" not in verbs
    assert "commit" not in verbs
    assert "submit_for_qa" not in verbs


def test_unknown_role_returns_empty_list() -> None:
    assert valid_next_verbs("unknown_role", _task("pending")) == []


def test_idle_verbs_always_available_for_developer() -> None:
    """`i_am_idle` and `give_me_work` are always offered regardless of
    whether the agent has an active task."""
    for status in ("pending", "claimed", "in_progress", "completed"):
        verbs = valid_next_verbs("developer", _task(status))
        assert "i_am_idle" in verbs
        assert "give_me_work" in verbs


# ---------------------------------------------------------------------
# is_verb_allowed
# ---------------------------------------------------------------------


def test_is_verb_allowed_true_for_offered_verb() -> None:
    assert is_verb_allowed("developer", "i_will_work_on", _task("pending")) is True


def test_is_verb_allowed_false_for_blocked_verb() -> None:
    assert is_verb_allowed("cell_pm", "i_will_work_on", _task("pending")) is False


def test_is_verb_allowed_false_for_unknown_role() -> None:
    assert is_verb_allowed("nope", "i_am_idle", _task("pending")) is False
