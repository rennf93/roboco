"""Tier 1 — spec self-tests. Fast (no DB, no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from roboco.lifecycle import spec
from roboco.models.base import TaskType as ModelTaskType


def test_role_enum_has_every_pre_gateway_role() -> None:
    """Every role from PERMISSIONS.md must be enumerated."""
    expected = {
        "developer",
        "qa",
        "documenter",
        "cell_pm",
        "main_pm",
        "product_owner",
        "head_marketing",
        "auditor",
        "ceo",
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
