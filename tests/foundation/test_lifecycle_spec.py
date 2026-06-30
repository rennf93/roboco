"""Tier 1 — spec self-tests. Fast (no DB, no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from roboco.foundation import _validate_lifecycle as _validate
from roboco.foundation._validate_lifecycle import reachable_from
from roboco.foundation.policy import lifecycle as spec
from roboco.foundation.policy.lifecycle import _INTENT_VERBS, IntentSpec
from roboco.models.base import TaskType as ModelTaskType


def test_role_enum_has_every_pre_gateway_role() -> None:
    """Every role from PERMISSIONS.md must be enumerated.

    The canonical Role enum is now defined in `roboco.foundation.identity`
    and re-exported here. It includes the 9 pre-gateway roles plus the
    SYSTEM sentinel used for orchestrator-generated rows. The pre-gateway
    PERMISSIONS.md is the historical canon — SYSTEM is the post-foundation
    addition that doesn't appear in policy tables.
    """
    expected = {
        "developer",
        "qa",
        "documenter",
        "cell_pm",
        "main_pm",
        "product_owner",
        "head_marketing",
        "auditor",
        "pr_reviewer",  # reviews inbound external/fork PRs (read-only)
        "prompter",  # post-gateway intake role (human-only, drafts tasks)
        "secretary",  # CEO's chief-of-staff (human-only, gated CEO authority)
        "ceo",
        "system",
    }
    actual = {r.value for r in spec.Role}
    assert actual == expected, f"Role enum drift: {actual ^ expected}"


def test_status_enum_has_every_pre_gateway_status() -> None:
    """Every status from STATUS_TRANSITIONS.md must be enumerated."""
    expected = {
        "backlog",
        "pending",
        "claimed",
        "in_progress",
        "blocked",
        "paused",
        "verifying",
        "awaiting_qa",
        "needs_revision",
        "awaiting_documentation",
        "awaiting_pr_review",
        "awaiting_pm_review",
        "awaiting_ceo_approval",
        "completed",
        "cancelled",
    }
    actual = {s.value for s in spec.Status}
    assert actual == expected, f"Status enum drift: {actual ^ expected}"


def test_task_type_enum_matches_models() -> None:
    """The spec's TaskType must match the existing models.base.TaskType.

    If the existing model adds/removes a type, the spec must be updated
    in lockstep — that's the entire point of this module.
    """
    spec_values = {t.value for t in spec.TaskType}
    model_values = {t.value for t in ModelTaskType}
    assert spec_values == model_values, (
        f"TaskType drift between lifecycle.spec and models.base: "
        f"{spec_values ^ model_values}"
    )


def test_decision_allow_has_no_rejection_kind() -> None:
    d = spec.Decision.allow()
    assert d.allowed is True
    assert d.rejection_kind is None
    assert d.message is None
    assert d.missing == []
    assert d.remediate is None


def test_decision_reject_requires_rejection_kind() -> None:
    d = spec.Decision.reject(
        kind="not_authorized",
        message="role 'developer' may not call delegate",
        remediate="only PMs delegate; call give_me_work() instead",
    )
    assert d.allowed is False
    assert d.rejection_kind == "not_authorized"
    assert d.message == "role 'developer' may not call delegate"
    assert d.remediate == "only PMs delegate; call give_me_work() instead"


def test_decision_tracing_gap_carries_missing_list() -> None:
    d = spec.Decision.tracing_gap(
        missing=["plan", "journal:decision"],
        remediate="provide plan and a journal:decision entry",
    )
    assert d.allowed is False
    assert d.rejection_kind == "tracing_gap"
    assert d.missing == ["plan", "journal:decision"]
    assert d.remediate == "provide plan and a journal:decision entry"


def test_decision_tracing_gap_defensively_copies_missing() -> None:
    """tracing_gap must isolate the stored list from the caller's source."""
    src = ["plan"]
    d = spec.Decision.tracing_gap(missing=src, remediate="r")
    src.append("mutated")
    assert d.missing == ["plan"]


def test_decision_invariants_enforced_at_construction() -> None:
    """allowed=True ⇒ rejection_kind None; allowed=False ⇒ kind set."""
    with pytest.raises(ValueError, match="allowed=True requires rejection_kind=None"):
        spec.Decision(
            allowed=True,
            rejection_kind="not_authorized",
            message="x",
            missing=[],
            remediate="x",
        )
    with pytest.raises(ValueError, match="allowed=False requires rejection_kind"):
        spec.Decision(
            allowed=False,
            rejection_kind=None,
            message="x",
            missing=[],
            remediate="x",
        )


def test_decision_invariant_rejects_allowed_with_missing_or_remediate() -> None:
    """allowed=True with missing or remediate set raises (Fix 1 lock-in)."""
    with pytest.raises(
        ValueError, match="allowed=True requires missing=\\[\\] and remediate=None"
    ):
        spec.Decision(
            allowed=True,
            rejection_kind=None,
            message=None,
            missing=["plan"],
            remediate=None,
        )
    with pytest.raises(
        ValueError,
        match="allowed=True requires missing=\\[\\] and remediate=None",
    ):
        spec.Decision(
            allowed=True,
            rejection_kind=None,
            message=None,
            missing=[],
            remediate="oops",
        )


def test_precondition_check_returns_bool() -> None:
    """A Precondition.check() is the gate-table evaluator."""
    p = spec.Precondition(
        key="commits>=1",
        check=lambda task, _agent, _ctx: bool(getattr(task, "commits", None)),
        remediate="commit at least once before opening a PR",
        missing_token="commits>=1",
    )

    task_with = SimpleNamespace(commits=["abc"])
    task_without = SimpleNamespace(commits=[])
    assert p.check(task_with, None, None) is True
    assert p.check(task_without, None, None) is False


def test_action_spec_holds_role_status_and_precondition_data() -> None:
    a = spec.ActionSpec(
        name="claim",
        allowed_roles=frozenset({spec.Role.DEVELOPER}),
        source_statuses=frozenset({spec.Status.PENDING, spec.Status.NEEDS_REVISION}),
        target_status=spec.Status.CLAIMED,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    )
    assert a.name == "claim"
    assert spec.Role.DEVELOPER in a.allowed_roles
    assert a.target_status == spec.Status.CLAIMED


def test_intent_spec_composes_atomic_actions() -> None:
    i = spec.IntentSpec(
        name="i_will_work_on",
        allowed_roles=frozenset({spec.Role.DEVELOPER}),
        description="Claim a task and start work on it.",
        composes=("claim", "set_plan", "start"),
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: "edit + commit, then open_pr",
    )
    assert i.composes == ("claim", "set_plan", "start")
    assert i.next_hint(None) == "edit + commit, then open_pr"


def test_status_transition_carries_role_constraint_optional() -> None:
    t = spec.StatusTransition(
        source=spec.Status.AWAITING_QA,
        target=spec.Status.AWAITING_DOCUMENTATION,
        triggered_by_action="qa_pass",
        role_constraint=frozenset({spec.Role.QA}),
    )
    assert t.source == spec.Status.AWAITING_QA
    assert t.target == spec.Status.AWAITING_DOCUMENTATION
    assert t.triggered_by_action == "qa_pass"
    assert t.role_constraint == frozenset({spec.Role.QA})


def test_status_transitions_includes_dev_path() -> None:
    """The dev happy path: pending → claimed → in_progress → verifying → awaiting_qa."""
    sources = {(t.source, t.target) for t in spec._STATUS_TRANSITIONS}
    assert (spec.Status.PENDING, spec.Status.CLAIMED) in sources
    assert (spec.Status.CLAIMED, spec.Status.IN_PROGRESS) in sources
    assert (spec.Status.IN_PROGRESS, spec.Status.VERIFYING) in sources
    assert (spec.Status.VERIFYING, spec.Status.AWAITING_QA) in sources


def test_status_transitions_includes_qa_paths() -> None:
    sources = {(t.source, t.target) for t in spec._STATUS_TRANSITIONS}
    assert (spec.Status.AWAITING_QA, spec.Status.CLAIMED) in sources  # QA claims
    assert (spec.Status.AWAITING_QA, spec.Status.AWAITING_DOCUMENTATION) in sources
    assert (spec.Status.AWAITING_QA, spec.Status.NEEDS_REVISION) in sources


def test_status_transitions_includes_ceo_paths() -> None:
    sources = {(t.source, t.target) for t in spec._STATUS_TRANSITIONS}
    assert (spec.Status.AWAITING_PM_REVIEW, spec.Status.COMPLETED) in sources
    assert (
        spec.Status.AWAITING_PM_REVIEW,
        spec.Status.AWAITING_CEO_APPROVAL,
    ) in sources
    assert (spec.Status.AWAITING_CEO_APPROVAL, spec.Status.COMPLETED) in sources
    assert (spec.Status.AWAITING_CEO_APPROVAL, spec.Status.NEEDS_REVISION) in sources
    # #100: a branchless coordination root rejected by the CEO routes to PENDING
    # (Main PM re-plans) — the edge is in the spec so the audited privileged
    # override that applies it can't be wedged by future admin-override tightening.
    assert (spec.Status.AWAITING_CEO_APPROVAL, spec.Status.PENDING) in sources
    # A blocked task the PM cannot resolve can also be surfaced to the CEO.
    assert (spec.Status.BLOCKED, spec.Status.AWAITING_CEO_APPROVAL) in sources


def test_status_transitions_includes_block_pause_paths() -> None:
    sources = {(t.source, t.target) for t in spec._STATUS_TRANSITIONS}
    assert (spec.Status.IN_PROGRESS, spec.Status.BLOCKED) in sources
    assert (spec.Status.IN_PROGRESS, spec.Status.PAUSED) in sources
    assert (spec.Status.BLOCKED, spec.Status.IN_PROGRESS) in sources
    assert (spec.Status.PAUSED, spec.Status.IN_PROGRESS) in sources


def test_every_non_terminal_status_can_be_cancelled() -> None:
    """PERMISSIONS.md says PM/CEO can cancel from any state."""
    cancellable = {
        t.source for t in spec._STATUS_TRANSITIONS if t.target == spec.Status.CANCELLED
    }
    non_terminal = set(spec.Status) - {spec.Status.COMPLETED, spec.Status.CANCELLED}
    assert non_terminal <= cancellable, (
        f"Statuses missing a cancel transition: {non_terminal - cancellable}"
    )


def test_status_graph_lookup_returns_targets() -> None:
    """STATUS_GRAPH is a quick `source -> {targets}` lookup."""
    assert spec.Status.CLAIMED in spec.STATUS_GRAPH[spec.Status.PENDING]
    assert spec.Status.AWAITING_QA in spec.STATUS_GRAPH[spec.Status.VERIFYING]
    assert spec.STATUS_GRAPH[spec.Status.COMPLETED] == frozenset()


def test_status_transitions_role_constraints_match_canon() -> None:
    """role_constraint must encode the per-row role gates from
    PERMISSIONS.md / STATUS_TRANSITIONS.md exactly. Tests that look only
    at (source, target) pairs miss role-typo regressions; this test
    pins the gates explicitly.
    """
    by_pair = {
        (t.source, t.target, t.triggered_by_action): t.role_constraint
        for t in spec._STATUS_TRANSITIONS
    }
    # QA is the only role that can claim awaiting_qa
    assert by_pair[
        (spec.Status.AWAITING_QA, spec.Status.CLAIMED, "claim")
    ] == frozenset({spec.Role.QA})
    # Documenter is the only role that can claim awaiting_documentation
    assert by_pair[
        (spec.Status.AWAITING_DOCUMENTATION, spec.Status.CLAIMED, "claim")
    ] == frozenset({spec.Role.DOCUMENTER})
    # qa_pass / qa_fail: QA only
    assert by_pair[
        (spec.Status.AWAITING_QA, spec.Status.AWAITING_DOCUMENTATION, "qa_pass")
    ] == frozenset({spec.Role.QA})
    assert by_pair[
        (spec.Status.AWAITING_QA, spec.Status.NEEDS_REVISION, "qa_fail")
    ] == frozenset({spec.Role.QA})
    # docs_complete: documenter only
    assert by_pair[
        (
            spec.Status.AWAITING_DOCUMENTATION,
            spec.Status.AWAITING_PM_REVIEW,
            "docs_complete",
        )
    ] == frozenset({spec.Role.DOCUMENTER})
    # PM complete: cell + main PM (not board, not CEO)
    assert by_pair[
        (spec.Status.AWAITING_PM_REVIEW, spec.Status.COMPLETED, "complete")
    ] == frozenset({spec.Role.CELL_PM, spec.Role.MAIN_PM})
    # escalate_to_ceo: main_pm + product_owner + head_marketing — from a
    # completed review and from a blocked task, same role gate.
    escalate_roles = frozenset(
        {
            spec.Role.MAIN_PM,
            spec.Role.PRODUCT_OWNER,
            spec.Role.HEAD_MARKETING,
        }
    )
    assert (
        by_pair[
            (
                spec.Status.AWAITING_PM_REVIEW,
                spec.Status.AWAITING_CEO_APPROVAL,
                "escalate_to_ceo",
            )
        ]
        == escalate_roles
    )
    assert (
        by_pair[
            (
                spec.Status.BLOCKED,
                spec.Status.AWAITING_CEO_APPROVAL,
                "escalate_to_ceo",
            )
        ]
        == escalate_roles
    )
    # CEO actions: CEO only
    assert by_pair[
        (spec.Status.AWAITING_CEO_APPROVAL, spec.Status.COMPLETED, "ceo_approve")
    ] == frozenset({spec.Role.CEO})
    assert by_pair[
        (spec.Status.AWAITING_CEO_APPROVAL, spec.Status.NEEDS_REVISION, "ceo_reject")
    ] == frozenset({spec.Role.CEO})
    # Cancel: PM + CEO from any non-terminal status EXCEPT the CEO approval
    # queue — cancelling a task the CEO is reviewing is the CEO's call, so
    # awaiting_ceo_approval -> cancelled is gated to CEO only (a PM cancelling
    # it would bypass the human CEO gate).
    cancel_constraint = frozenset({spec.Role.CELL_PM, spec.Role.MAIN_PM, spec.Role.CEO})
    for src in spec.Status:
        if src in (spec.Status.COMPLETED, spec.Status.CANCELLED):
            continue
        expected = (
            frozenset({spec.Role.CEO})
            if src is spec.Status.AWAITING_CEO_APPROVAL
            else cancel_constraint
        )
        assert by_pair[(src, spec.Status.CANCELLED, "cancel")] == expected, (
            f"cancel from {src.value} has wrong role_constraint"
        )


def test_atomic_action_table_has_pre_gateway_actions() -> None:
    """Every task tool from PERMISSIONS.md must have an ActionSpec."""
    expected = {
        "activate",
        "claim",
        "start",
        "set_plan",
        "block",
        "unblock",
        "pause",
        "resume",
        "submit_verification",
        "submit_qa",
        "qa_pass",
        "qa_fail",
        "docs_complete",
        "complete",
        "submit_pm_review",
        "escalate_to_ceo",
        "ceo_approve",
        "ceo_reject",
        "cancel",
        "create_subtask",
    }
    assert expected <= set(spec._ATOMIC_ACTIONS), (
        f"Missing ActionSpec entries: {expected - set(spec._ATOMIC_ACTIONS)}"
    )


def test_claim_action_allows_developer_from_pending() -> None:
    a = spec._ATOMIC_ACTIONS["claim"]
    assert spec.Role.DEVELOPER in a.allowed_roles
    assert spec.Status.PENDING in a.source_statuses
    assert a.target_status == spec.Status.CLAIMED


def test_qa_pass_self_review_blocks() -> None:
    """A QA cannot qa_pass a task they themselves committed to."""
    assert spec._ATOMIC_ACTIONS["qa_pass"].self_review_block is True
    assert spec._ATOMIC_ACTIONS["qa_fail"].self_review_block is True
    assert spec._ATOMIC_ACTIONS["docs_complete"].self_review_block is True


def test_claim_rules_match_pre_gateway_table() -> None:
    """PERMISSIONS.md "What Each Role Can Claim From" — exact match.

    PMs claim from PENDING and NEEDS_REVISION — the latter to recover a rejected
    coordination task (pr_fail / qa_fail / ceo_reject) by re-planning and
    re-delegating fixes (scoped by give_me_work routing, which offers only the
    caller's own assigned tasks). BACKLOG → PENDING is a separate `activate`
    action (strict transitions; no implicit activate-on-claim).
    """
    assert spec.CLAIM_RULES[spec.Role.DEVELOPER] == frozenset(
        {spec.Status.PENDING, spec.Status.NEEDS_REVISION}
    )
    assert spec.CLAIM_RULES[spec.Role.QA] == frozenset({spec.Status.AWAITING_QA})
    assert spec.CLAIM_RULES[spec.Role.DOCUMENTER] == frozenset(
        {spec.Status.PENDING, spec.Status.AWAITING_DOCUMENTATION}
    )
    assert spec.CLAIM_RULES[spec.Role.CELL_PM] == frozenset(
        {spec.Status.PENDING, spec.Status.NEEDS_REVISION}
    )
    assert spec.CLAIM_RULES[spec.Role.MAIN_PM] == frozenset(
        {spec.Status.PENDING, spec.Status.NEEDS_REVISION}
    )


def test_team_rules_pin_team_for_seeded_agents() -> None:
    assert spec.ROLE_TEAM_RULES["be-dev-1"] == "backend"
    assert spec.ROLE_TEAM_RULES["be-pm"] == "backend"
    assert spec.ROLE_TEAM_RULES["fe-qa"] == "frontend"
    assert spec.ROLE_TEAM_RULES["main-pm"] is None  # cross-cell


def test_intent_verbs_table_has_every_gateway_verb() -> None:
    """Every gateway intent verb must have an IntentSpec."""
    expected = {
        "give_me_work",
        "i_will_work_on",
        "i_will_plan",
        "delegate",
        "open_pr",
        "i_am_done",
        "i_am_blocked",
        "unclaim",
        "resume",
        "i_am_idle",
        "claim_review",
        "pass_review",
        "fail_review",
        "claim_doc_task",
        "i_documented",
        "complete",
        "escalate_up",
        "escalate_to_ceo",
        "submit_up",
        "unblock",
        "triage",
        "triage_all",
    }
    assert expected <= set(spec._INTENT_VERBS), (
        f"Missing IntentSpec entries: {expected - set(spec._INTENT_VERBS)}"
    )


def test_i_will_work_on_composes_claim_set_plan_start() -> None:
    iv = spec._INTENT_VERBS["i_will_work_on"]
    assert iv.composes == ("claim", "set_plan", "start")
    assert spec.Role.DEVELOPER in iv.allowed_roles


def test_i_will_plan_composes_claim_set_plan_start() -> None:
    """PMs use i_will_plan; the composition mirrors i_will_work_on."""
    iv = spec._INTENT_VERBS["i_will_plan"]
    assert iv.composes == ("claim", "set_plan", "start")
    assert iv.allowed_roles == frozenset({spec.Role.CELL_PM, spec.Role.MAIN_PM})


def test_i_am_done_composes_submit_verification_then_submit_qa() -> None:
    iv = spec._INTENT_VERBS["i_am_done"]
    assert iv.composes == ("submit_verification", "submit_qa")


def test_open_pr_has_git_side_effects() -> None:
    """open_pr is a side-effect-only verb (no DB transition)."""
    iv = spec._INTENT_VERBS["open_pr"]
    assert "push_branch" in iv.side_effects
    assert "create_pr" in iv.side_effects
    assert iv.composes == ()  # pure side effect verb


def test_delegate_composes_create_subtask() -> None:
    iv = spec._INTENT_VERBS["delegate"]
    assert iv.composes == ("create_subtask",)
    assert iv.allowed_roles == frozenset({spec.Role.CELL_PM, spec.Role.MAIN_PM})


_STUB_TASK_DEFAULTS: dict[str, Any] = {
    "status": "pending",
    "task_type": "code",
    "commits": [],
    "plan": None,
    "assigned_to": None,
    "pr_number": None,
}


def _stub_task(**overrides: Any) -> SimpleNamespace:
    fields = {**_STUB_TASK_DEFAULTS, **overrides}
    fields["commits"] = fields["commits"] or []
    return SimpleNamespace(**fields)


def test_can_claim_developer_pending_allowed() -> None:
    d = spec.can_claim(spec.Role.DEVELOPER, _stub_task(status="pending"))
    assert d.allowed is True


def test_can_claim_developer_completed_rejected() -> None:
    d = spec.can_claim(spec.Role.DEVELOPER, _stub_task(status="completed"))
    assert d.allowed is False
    assert d.rejection_kind == "invalid_state"


def test_can_claim_developer_awaiting_qa_rejected() -> None:
    """Devs cannot claim awaiting_qa - that's QA's path."""
    d = spec.can_claim(spec.Role.DEVELOPER, _stub_task(status="awaiting_qa"))
    assert d.allowed is False
    assert d.rejection_kind == "not_authorized"


def test_can_invoke_intent_developer_can_call_i_will_work_on() -> None:
    d = spec.can_invoke_intent(
        spec.Role.DEVELOPER,
        "i_will_work_on",
        _stub_task(status="pending"),
        context=spec.Context(plan="my plan"),
    )
    assert d.allowed is True


def test_can_invoke_intent_pm_cannot_call_i_will_work_on() -> None:
    """PMs use i_will_plan; i_will_work_on is dev-only."""
    d = spec.can_invoke_intent(
        spec.Role.CELL_PM,
        "i_will_work_on",
        _stub_task(status="pending"),
        context=spec.Context(plan="x"),
    )
    assert d.allowed is False
    assert d.rejection_kind == "not_authorized"


def test_can_invoke_intent_developer_open_pr_no_commits_tracing_gap() -> None:
    """open_pr requires >=1 commit. Without one -> tracing_gap."""
    d = spec.can_invoke_intent(
        spec.Role.DEVELOPER,
        "open_pr",
        _stub_task(status="in_progress", commits=[]),
        context=spec.Context(),
    )
    assert d.allowed is False
    assert d.rejection_kind == "tracing_gap"
    assert "commits>=1" in d.missing


# --------------------------------------------------------------------------- #
# open_pr must enforce the PR-open state gate (parity with the HTTP path)
# --------------------------------------------------------------------------- #


def _owned_task(**overrides: Any) -> SimpleNamespace:
    """A task owned by ``actor`` with commits and no prior PR — only the state
    gate can fail, isolating the PR-open-state precondition."""
    actor = overrides.pop("actor_id", uuid4())
    return _stub_task(
        assigned_to=actor,
        commits=["abc123"],
        pr_number=None,
        **overrides,
    )


def test_open_pr_rejected_on_claimed_task() -> None:
    """``open_pr`` must be rejected from ``claimed`` — only ``in_progress`` may
    open a PR (mirrors the HTTP path's ``_assert_pr_create_allowed``)."""
    actor = uuid4()
    d = spec.can_invoke_intent(
        spec.Role.DEVELOPER,
        "open_pr",
        _owned_task(status="claimed", actor_id=actor),
        context=spec.Context(actor_id=actor),
    )
    assert d.allowed is False
    assert d.rejection_kind == "invalid_state"


def test_open_pr_rejected_on_paused_task() -> None:
    actor = uuid4()
    d = spec.can_invoke_intent(
        spec.Role.DEVELOPER,
        "open_pr",
        _owned_task(status="paused", actor_id=actor),
        context=spec.Context(actor_id=actor),
    )
    assert d.allowed is False
    assert d.rejection_kind == "invalid_state"


def test_open_pr_rejected_on_blocked_task() -> None:
    actor = uuid4()
    d = spec.can_invoke_intent(
        spec.Role.DEVELOPER,
        "open_pr",
        _owned_task(status="blocked", actor_id=actor),
        context=spec.Context(actor_id=actor),
    )
    assert d.allowed is False
    assert d.rejection_kind == "invalid_state"


def test_open_pr_rejected_on_completed_task() -> None:
    """A completed task is terminal — opening a PR on it is nonsensical."""
    actor = uuid4()
    d = spec.can_invoke_intent(
        spec.Role.DEVELOPER,
        "open_pr",
        _owned_task(status="completed", actor_id=actor),
        context=spec.Context(actor_id=actor),
    )
    assert d.allowed is False
    assert d.rejection_kind == "invalid_state"


@pytest.mark.parametrize(
    "status",
    [
        "in_progress",
        "verifying",
        "awaiting_qa",
        "awaiting_documentation",
        "needs_revision",
    ],
)
def test_open_pr_allowed_in_pr_open_states(status: str) -> None:
    """Regression guard: every PR-open-eligible state still lets the owner open
    a PR — the new state gate must not over-restrict the legitimate path."""
    actor = uuid4()
    d = spec.can_invoke_intent(
        spec.Role.DEVELOPER,
        "open_pr",
        _owned_task(status=status, actor_id=actor),
        context=spec.Context(actor_id=actor),
    )
    assert d.allowed is True, f"open_pr should be allowed from {status}"


def test_open_pr_state_gate_takes_priority_over_unowned() -> None:
    """A non-owner in a wrong state: ownership (not_authorized) is checked
    before state, mirroring the HTTP path's assignee-first ordering."""
    d = spec.can_invoke_intent(
        spec.Role.DEVELOPER,
        "open_pr",
        _owned_task(status="claimed", actor_id=uuid4()),
        context=spec.Context(actor_id=uuid4()),  # different actor -> not owner
    )
    assert d.allowed is False
    assert d.rejection_kind == "not_authorized"


def test_escalate_up_rejected_on_completed_task() -> None:
    """A PM must not resurrect a COMPLETED task via ``escalate_up`` — the spec
    gate rejects terminal tasks before the journal:decision write fires."""
    d = spec.can_invoke_intent(
        spec.Role.CELL_PM,
        "escalate_up",
        _stub_task(status="completed"),
        context=spec.Context(notes="stuck on something"),
    )
    assert d.allowed is False
    assert d.rejection_kind == "invalid_state"


def test_escalate_up_rejected_on_cancelled_task() -> None:
    """Cancelled is terminal — escalate_up must not resurrect it either."""
    d = spec.can_invoke_intent(
        spec.Role.MAIN_PM,
        "escalate_up",
        _stub_task(status="cancelled"),
        context=spec.Context(notes="stuck on something"),
    )
    assert d.allowed is False
    assert d.rejection_kind == "invalid_state"


def test_escalate_up_allowed_on_blocked_task() -> None:
    """The terminal guard must not over-restrict — BLOCKED is the natural
    escalation source and must still be allowed."""
    d = spec.can_invoke_intent(
        spec.Role.CELL_PM,
        "escalate_up",
        _stub_task(status="blocked"),
        context=spec.Context(notes="stuck on something"),
    )
    assert d.allowed is True


def test_valid_next_verbs_developer_in_progress_includes_open_pr_and_i_am_done() -> (
    None
):
    verbs = spec.valid_next_verbs(spec.Role.DEVELOPER, _stub_task(status="in_progress"))
    assert "open_pr" in verbs
    assert "i_am_done" in verbs
    assert "i_am_blocked" in verbs


def test_valid_next_verbs_pm_pending_includes_i_will_plan() -> None:
    # A PM i_will_plan's a PLANNING task (coordination), not a code task — the
    # PM/code claim carve-out (Fix 2) removes i_will_plan from a pending code
    # task's verb set, so the legitimate path is exercised with task_type=planning.
    verbs = spec.valid_next_verbs(
        spec.Role.CELL_PM, _stub_task(status="pending", task_type="planning")
    )
    assert "i_will_plan" in verbs


def test_composed_actions_for_returns_intent_composition() -> None:
    assert spec.composed_actions_for("i_will_work_on") == ("claim", "set_plan", "start")
    assert spec.composed_actions_for("open_pr") == ()


def test_intents_for_role_returns_role_scoped_verbs() -> None:
    dev_verbs = spec.intents_for_role(spec.Role.DEVELOPER)
    assert "i_will_work_on" in dev_verbs
    assert "open_pr" in dev_verbs
    assert "i_am_done" in dev_verbs
    assert "delegate" not in dev_verbs  # PM only
    assert "claim_review" not in dev_verbs  # QA only


def test_status_after_returns_target_status() -> None:
    assert spec.status_after("claim", spec.Status.PENDING) == spec.Status.CLAIMED
    assert (
        spec.status_after("submit_qa", spec.Status.VERIFYING) == spec.Status.AWAITING_QA
    )
    assert (
        spec.status_after("set_plan", spec.Status.IN_PROGRESS) is None
    )  # no transition


def test_can_invoke_intent_open_pr_passes_when_owner_with_commits() -> None:
    """Green path for open_pr: owner + commits + no prior PR → allow."""
    owner_id = uuid4()
    task = _stub_task(
        status="in_progress",
        commits=["abc"],
        pr_number=None,
        assigned_to=owner_id,
    )
    d = spec.can_invoke_intent(
        spec.Role.DEVELOPER,
        "open_pr",
        task,
        context=spec.Context(actor_id=owner_id),
    )
    assert d.allowed is True, f"expected allow, got {d}"


def test_can_invoke_intent_open_pr_rejects_non_owner() -> None:
    """Non-owner trying open_pr → not_authorized (PRECONDITION_OWNERSHIP)."""
    owner_id = uuid4()
    intruder_id = uuid4()
    task = _stub_task(
        status="in_progress",
        commits=["abc"],
        pr_number=None,
        assigned_to=owner_id,
    )
    d = spec.can_invoke_intent(
        spec.Role.DEVELOPER,
        "open_pr",
        task,
        context=spec.Context(actor_id=intruder_id),
    )
    assert d.allowed is False
    assert d.rejection_kind == "not_authorized"


# ---------------------------------------------------------------------------
# Task 8 — self-consistency validators (`_validate.py`)
# ---------------------------------------------------------------------------


def test_validators_pass_on_real_spec() -> None:
    """Importing roboco.foundation.policy.lifecycle must not raise —
    module-level import IS the test. We additionally call the runner
    directly so a future refactor that detaches it from import doesn't
    silently skip the gate.
    """
    _validate.run_all_lifecycle_validators()


def test_every_status_reachable_from_pending() -> None:
    """Reachability — except CANCELLED is its own thing and BACKLOG predates pending."""
    reachable = reachable_from(spec.Status.PENDING)
    expected_reachable = set(spec.Status) - {spec.Status.BACKLOG, spec.Status.CANCELLED}
    assert expected_reachable <= reachable, (
        f"Unreachable from pending: {expected_reachable - reachable}"
    )


def test_every_intent_verb_composes_known_actions() -> None:
    """Every IntentSpec.composes must reference declared atomic actions."""
    for name, iv in spec._INTENT_VERBS.items():
        for action_name in iv.composes:
            assert action_name in spec._ATOMIC_ACTIONS, (
                f"Intent '{name}' composes unknown action '{action_name}'"
            )


def test_self_review_symmetry() -> None:
    """If qa_pass blocks, qa_fail and docs_complete must too."""
    qp = spec._ATOMIC_ACTIONS["qa_pass"].self_review_block
    qf = spec._ATOMIC_ACTIONS["qa_fail"].self_review_block
    dc = spec._ATOMIC_ACTIONS["docs_complete"].self_review_block
    assert qp == qf == dc, (
        "self_review_block asymmetry between qa_pass/qa_fail/docs_complete"
    )


def test_run_all_validators_raises_on_unknown_intent_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If an IntentSpec.composes references a non-existent action, the
    validator must raise LifecycleSpecError. Pins the gate's actual
    behavior — without this test, refactors that move run_all_validators()
    out of the import path could silently disable the gate.
    """
    iv = _INTENT_VERBS["delegate"]
    broken = IntentSpec(
        name=iv.name,
        allowed_roles=iv.allowed_roles,
        description=iv.description,
        composes=("create_subtask", "ZZZ_FAKE_ACTION_DOES_NOT_EXIST"),
        extra_preconditions=iv.extra_preconditions,
        side_effects=iv.side_effects,
        next_hint=iv.next_hint,
    )
    patched_intents = dict(_INTENT_VERBS)
    patched_intents["delegate"] = broken
    monkeypatch.setattr(
        "roboco.foundation.policy.lifecycle._INTENT_VERBS", patched_intents
    )
    with pytest.raises(_validate.LifecycleSpecError, match="ZZZ_FAKE_ACTION"):
        _validate.run_all_lifecycle_validators()


def test_next_hint_pr_fail_main_pm_root_steers_to_redelegate() -> None:
    """A ``pr_fail`` on a Main-PM branch-bearing root must steer the Main PM to
    re-delegate the fixes, NOT re-submit the unchanged root. The root is an
    assembled cell→root / root→master PR — coordination, not the Main PM's own
    code — so re-submitting it is the 2026-06-27 infinite ``pr_fail`` loop."""
    t = SimpleNamespace(team=spec.Team.MAIN_PM, branch_name="feature/main_pm/c80e19ff")
    hint = _INTENT_VERBS["pr_fail"].next_hint(t)
    assert "re-delegate" in hint
    assert "do NOT re-submit" in hint


def test_next_hint_pr_fail_cell_dev_keeps_dev_revise() -> None:
    """A cell / dev task is revised in place by its dev, so ``pr_fail`` keeps the
    dev-revise hint (the cell→root PR carries that dev's own code)."""
    t = SimpleNamespace(team=spec.Team.BACKEND, branch_name="feature/backend/abc12345")
    hint = _INTENT_VERBS["pr_fail"].next_hint(t)
    assert hint == "idle - dev will revise and re-submit"


def test_next_hint_pr_fail_branchless_main_pm_keeps_dev_revise() -> None:
    """A branchless Main-PM umbrella (no ``branch_name``) assembles no PR of its
    own, so the gate never lands a ``pr_fail`` on it — but defensively it keeps
    the dev-revise hint rather than the re-delegate steer."""
    t = SimpleNamespace(team=spec.Team.MAIN_PM, branch_name=None)
    hint = _INTENT_VERBS["pr_fail"].next_hint(t)
    assert hint == "idle - dev will revise and re-submit"


def test_unmigrated_is_pinned() -> None:
    """The known-debt set; remove an entry once that consumer is migrated."""
    assert (
        frozenset(
            {
                "enforcement.task_lifecycle._LEGACY_OPERATIONAL_EDGES",
                "enforcement.task_lifecycle._LEGACY_ROLE_GATES",
            }
        )
        == spec.UNMIGRATED
    )


# --- PM/code claim invariant (Fix 2 + bug-1): the claim gate does NOT block a
# PM claiming a code task. A PM's only claim verb is i_will_plan, and planning a
# code-typed PARENT (to decompose + delegate the code) is legitimate (bug-1:
# scoping pm_cannot_execute_code to i_will_plan deadlocked the slice). Execution
# is blocked at the intent level — i_will_work_on is _DEV_ROLES only. The
# create/delegate guards (pm_cannot_own_code) block a PM from being ASSIGNED a
# fresh code task; the needs_revision carve-out (a PM resolving review issues
# directly / recovering a rejected coordination task) is naturally allowed
# because PMs claim NEEDS_REVISION. These pin that the claim gate does not
# regress bug-1.


def _claim_task(*, status: str, task_type: str) -> Any:
    return SimpleNamespace(status=status, task_type=task_type)


def test_claim_allows_cell_pm_claiming_code_from_pending() -> None:
    """bug-1: a cell PM i_will_plan-ing a code-typed parent (PENDING) to plan +
    delegate the code MUST be allowed — rejecting it deadlocks the slice."""
    t = _claim_task(status="pending", task_type="code")
    d = spec.can_invoke_action(spec.Role.CELL_PM, "claim", t)
    assert d.allowed, d.message


def test_claim_allows_cell_pm_claiming_code_from_needs_revision() -> None:
    """Carve-out: a PM may take a code task in needs_revision to resolve the
    review/QA issues directly / recover a rejected coordination task."""
    t = _claim_task(status="needs_revision", task_type="code")
    d = spec.can_invoke_action(spec.Role.CELL_PM, "claim", t)
    assert d.allowed, d.message


def test_claim_allows_main_pm_claiming_code_from_needs_revision() -> None:
    """The same carve-out holds for the Main PM (coordination-recovery path)."""
    t = _claim_task(status="needs_revision", task_type="code")
    d = spec.can_invoke_action(spec.Role.MAIN_PM, "claim", t)
    assert d.allowed, d.message


def test_claim_allows_main_pm_claiming_code_from_pending() -> None:
    """bug-1 parity: a Main PM planning a code-typed parent (PENDING) is allowed
    for the same reason as the cell PM — execution is blocked at i_will_work_on,
    not at the claim gate."""
    t = _claim_task(status="pending", task_type="code")
    d = spec.can_invoke_action(spec.Role.MAIN_PM, "claim", t)
    assert d.allowed, d.message


def test_claim_allows_pm_claiming_planning_from_pending() -> None:
    """A PM claiming a planning task is the legitimate coordination path."""
    t = _claim_task(status="pending", task_type="planning")
    assert spec.can_invoke_action(spec.Role.CELL_PM, "claim", t).allowed
    assert spec.can_invoke_action(spec.Role.MAIN_PM, "claim", t).allowed


def test_claim_allows_developer_claiming_code_from_pending() -> None:
    """A developer claiming fresh code is unaffected (the PM invariant is
    enforced at create/delegate + i_will_work_on, not the claim gate)."""
    t = _claim_task(status="pending", task_type="code")
    assert spec.can_invoke_action(spec.Role.DEVELOPER, "claim", t).allowed


# ---------------------------------------------------------------------------
# Edge cases — logical-gap element sweep (2026-06-30)
# ---------------------------------------------------------------------------


def test_claim_pr_review_rejected_on_gate_task_points_to_claim_gate_review() -> None:
    """claim_pr_review is for an inbound external-PR task in PENDING only. An
    awaiting_pr_review gate task must be rejected (and remediation must point
    the reviewer at claim_gate_review), not silently accepted by the spec gate."""
    d = spec.can_invoke_intent(
        spec.Role.PR_REVIEWER,
        "claim_pr_review",
        _stub_task(status="awaiting_pr_review"),
    )
    assert d.allowed is False
    assert d.rejection_kind == "invalid_state"
    assert "claim_gate_review" in (d.remediate or "")


def test_claim_pr_review_allowed_on_pending_external_review() -> None:
    """Green path: a pending external-PR review task is claimable."""
    d = spec.can_invoke_intent(
        spec.Role.PR_REVIEWER,
        "claim_pr_review",
        _stub_task(status="pending"),
    )
    assert d.allowed is True


def test_needs_team_match_rejects_cross_team_claim_when_agent_team_supplied() -> None:
    """needs_team_match was a dead spec field; when the caller supplies the
    agent's team via Context, the spec gate must enforce it (a backend dev
    cannot claim a frontend task)."""
    d = spec.can_invoke_action(
        spec.Role.DEVELOPER,
        "claim",
        _stub_task(status="pending", team="frontend"),
        context=spec.Context(agent_team="backend"),
    )
    assert d.allowed is False
    assert d.rejection_kind == "not_authorized"


def test_needs_team_match_allows_same_team_claim() -> None:
    d = spec.can_invoke_action(
        spec.Role.DEVELOPER,
        "claim",
        _stub_task(status="pending", team="backend"),
        context=spec.Context(agent_team="backend"),
    )
    assert d.allowed is True


def test_needs_team_match_defers_when_agent_team_absent() -> None:
    """Backward compat: without agent_team in Context, the spec gate stays
    permissive (the service layer still enforces team-match)."""
    d = spec.can_invoke_action(
        spec.Role.DEVELOPER,
        "claim",
        _stub_task(status="pending", team="frontend"),
    )
    assert d.allowed is True


def test_valid_next_verbs_omits_claim_review_when_qa_not_in_awaiting_qa() -> None:
    """valid_next_verbs must apply claim-rule narrowing for empty-compose
    claim verbs; a QA reviewer on a COMPLETED task must not be told
    claim_review is callable."""
    verbs = spec.valid_next_verbs(spec.Role.QA, _stub_task(status="completed"))
    assert "claim_review" not in verbs


def test_valid_next_verbs_includes_claim_review_for_qa_in_awaiting_qa() -> None:
    verbs = spec.valid_next_verbs(spec.Role.QA, _stub_task(status="awaiting_qa"))
    assert "claim_review" in verbs


def test_pr_reviewer_has_unclaim_release_verb() -> None:
    """A PR reviewer who cannot finish a review must have a self-release
    verb (unclaim), not wedge the lane until the stale-claim reaper."""
    assert "unclaim" in spec.intents_for_role(spec.Role.PR_REVIEWER)


def test_unclaim_allowed_for_pr_reviewer() -> None:
    d = spec.can_invoke_intent(
        spec.Role.PR_REVIEWER,
        "unclaim",
        _stub_task(status="awaiting_pr_review"),
    )
    assert d.allowed is True


def test_complete_intent_declares_no_inverted_pr_merge_side_effect() -> None:
    """complete's IntentSpec must not declare a trailing pr_merge side_effect:
    TaskService.complete asserts the PR is already merged, so the merge runs
    FIRST (choreographer verb body owns the ordering). The spec must match
    reality, not lie about a complete-then-merge composition."""
    iv = spec._INTENT_VERBS["complete"]
    assert iv.composes == ("complete",)
    assert iv.side_effects == ()
