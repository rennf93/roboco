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
    # Cancel: PM + CEO from any non-terminal status
    cancel_constraint = frozenset({spec.Role.CELL_PM, spec.Role.MAIN_PM, spec.Role.CEO})
    for src in spec.Status:
        if src in (spec.Status.COMPLETED, spec.Status.CANCELLED):
            continue
        assert by_pair[(src, spec.Status.CANCELLED, "cancel")] == cancel_constraint, (
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

    PMs claim from PENDING only; BACKLOG → PENDING is a separate `activate`
    action (strict transitions; no implicit activate-on-claim).
    """
    assert spec.CLAIM_RULES[spec.Role.DEVELOPER] == frozenset(
        {spec.Status.PENDING, spec.Status.NEEDS_REVISION}
    )
    assert spec.CLAIM_RULES[spec.Role.QA] == frozenset({spec.Status.AWAITING_QA})
    assert spec.CLAIM_RULES[spec.Role.DOCUMENTER] == frozenset(
        {spec.Status.PENDING, spec.Status.AWAITING_DOCUMENTATION}
    )
    assert spec.CLAIM_RULES[spec.Role.CELL_PM] == frozenset({spec.Status.PENDING})
    assert spec.CLAIM_RULES[spec.Role.MAIN_PM] == frozenset({spec.Status.PENDING})


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


def test_valid_next_verbs_developer_in_progress_includes_open_pr_and_i_am_done() -> (
    None
):
    verbs = spec.valid_next_verbs(spec.Role.DEVELOPER, _stub_task(status="in_progress"))
    assert "open_pr" in verbs
    assert "i_am_done" in verbs
    assert "i_am_blocked" in verbs


def test_valid_next_verbs_pm_pending_includes_i_will_plan() -> None:
    verbs = spec.valid_next_verbs(spec.Role.CELL_PM, _stub_task(status="pending"))
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
    """Non-owner trying open_pr → tracing_gap with owns_task missing."""
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
    assert d.rejection_kind == "tracing_gap"
    assert "owns_task" in d.missing


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
