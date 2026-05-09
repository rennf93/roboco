"""Tier 1 — spec self-tests. Fast (no DB, no network)."""

from __future__ import annotations

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
