"""Tier 1 — spec self-tests. Fast (no DB, no network)."""

from __future__ import annotations

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
