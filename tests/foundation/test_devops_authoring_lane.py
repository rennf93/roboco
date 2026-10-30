"""Stage-1 devops authoring lane: Role.DEVOPS rides the developer lifecycle.

A devops-authored infra task is TaskType.code and must pass the SAME spec
gates a developer task does: claim (i_will_work_on) -> open_pr (PR before
QA) -> i_am_done (submit_verification + submit_qa composition), plus the
needs_revision reclaim after a QA fail. The devops agent gains NO QA or
reviewer verbs here: its only review surface is the gate trio
(claim_gate_review / pr_pass / pr_fail), and the infra-matched precondition
on those is Stage 3.

Flag note: these spec mirrors are unconditional, matching Stage 0's approach
(DEVOPS verbs were granted at the spec layer with no flag). Flag-off inertia
comes from the runtime: the dispatchers never spawn devops-1 while
ROBOCO_DEVOPS_ENABLED is off.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

from roboco.foundation.policy import lifecycle as spec

DEVOPS_UUID = UUID("00000000-0000-0000-0004-000000000009")
DEV_UUID = UUID("00000000-0000-0000-0004-000000000001")

_STUB_DEFAULTS: dict[str, Any] = {
    "status": "pending",
    "task_type": "code",
    "team": "backend",
    "commits": [],
    "plan": None,
    "assigned_to": None,
    "pr_number": None,
}


def _task(**overrides: Any) -> SimpleNamespace:
    fields = {**_STUB_DEFAULTS, **overrides}
    fields["commits"] = fields["commits"] or []
    return SimpleNamespace(**fields)


def test_claim_rules_devops_mirror_developer_author_edges() -> None:
    """PENDING (delegated/materialized work) + NEEDS_REVISION (rework cycle,
    the developer set) plus the AWAITING_PR_REVIEW gate-review edge."""
    assert spec.CLAIM_RULES[spec.Role.DEVOPS] == frozenset(
        {
            spec.Status.PENDING,
            spec.Status.NEEDS_REVISION,
            spec.Status.AWAITING_PR_REVIEW,
        }
    )


def test_devops_i_will_work_on_from_pending_allowed() -> None:
    d = spec.can_invoke_intent(
        spec.Role.DEVOPS,
        "i_will_work_on",
        _task(status="pending", assigned_to=DEVOPS_UUID),
        context=spec.Context(plan="harden the compose file", actor_id=DEVOPS_UUID),
    )
    assert d.allowed is True, d.message


def test_devops_reclaim_from_needs_revision_allowed() -> None:
    """The QA-fail rework cycle: a bounced devops task must be re-claimable,
    exactly like a developer's leaf revision."""
    d = spec.can_invoke_intent(
        spec.Role.DEVOPS,
        "i_will_work_on",
        _task(status="needs_revision", assigned_to=DEVOPS_UUID),
        context=spec.Context(plan="fix the findings", actor_id=DEVOPS_UUID),
    )
    assert d.allowed is True, d.message


def test_devops_open_pr_allowed() -> None:
    """PR before QA: the devops author opens the PR itself, like a dev."""
    d = spec.can_invoke_intent(
        spec.Role.DEVOPS,
        "open_pr",
        _task(
            status="in_progress",
            assigned_to=DEVOPS_UUID,
            commits=["abc1234"],
        ),
        context=spec.Context(actor_id=DEVOPS_UUID),
    )
    assert d.allowed is True, d.message


def test_devops_i_am_done_composed_path_allowed() -> None:
    """i_am_done composes (submit_verification, submit_qa); both atomic
    actions must carry DEVOPS or the composed gate rejects what the intent
    gate allows (the Stage 0 gap this lane closes)."""
    d = spec.can_invoke_intent(
        spec.Role.DEVOPS,
        "i_am_done",
        _task(
            status="in_progress",
            assigned_to=DEVOPS_UUID,
            pr_number=42,
            commits=["abc1234"],
        ),
        context=spec.Context(actor_id=DEVOPS_UUID),
    )
    assert d.allowed is True, d.message


def test_devops_author_verbs_rejected_on_foreign_task() -> None:
    """Ownership precondition still applies: devops is not above it."""
    d = spec.can_invoke_intent(
        spec.Role.DEVOPS,
        "i_am_done",
        _task(status="in_progress", assigned_to=DEV_UUID, pr_number=1),
        context=spec.Context(actor_id=DEVOPS_UUID),
    )
    assert d.allowed is False
    assert d.rejection_kind == "not_authorized"


def test_devops_cannot_call_qa_or_reviewer_verbs() -> None:
    """No QA surface and no external-PR reviewer surface: devops reviews only
    at the gate (Stage 3 adds the infra-matched precondition there)."""
    task = _task(status="awaiting_qa", assigned_to=DEVOPS_UUID)
    for verb in ("claim_review", "pass_review", "fail_review", "claim_doc_task"):
        d = spec.can_invoke_intent(spec.Role.DEVOPS, verb, task)
        assert d.allowed is False, verb
        assert d.rejection_kind == "not_authorized", verb


def test_devops_gate_review_trio_still_granted() -> None:
    d = spec.can_invoke_intent(
        spec.Role.DEVOPS,
        "claim_gate_review",
        _task(status="awaiting_pr_review"),
    )
    assert d.allowed is True, d.message


def test_devops_valid_next_verbs_cover_the_author_path() -> None:
    """On a pending task the manifest steers devops to claim/work like a dev;
    on an open-PR in_progress task it can finish via i_am_done."""
    pending = spec.valid_next_verbs(
        spec.Role.DEVOPS,
        _task(status="pending", assigned_to=DEVOPS_UUID),
    )
    assert "i_will_work_on" in pending
    assert "give_me_work" in pending
    working = spec.valid_next_verbs(
        spec.Role.DEVOPS,
        _task(
            status="in_progress",
            assigned_to=DEVOPS_UUID,
            pr_number=42,
            commits=["abc1234"],
        ),
    )
    assert "i_am_done" in working
    assert "open_pr" in working
