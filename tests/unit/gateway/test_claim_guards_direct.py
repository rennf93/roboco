"""Direct unit tests for claim_guards helpers (branches only)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from roboco.services.gateway.claim_guards import (
    role_typed_claim_guard,
    sibling_sequence_guard,
)


def test_role_typed_claim_guard_pm_role_skipped() -> None:
    """Line 125: pm_role returns None (no role-type guard)."""
    assert role_typed_claim_guard("cell_pm", "code") is None


def test_role_typed_claim_guard_unknown_role_skipped() -> None:
    """Line 129: unknown role returns None (default-allow)."""
    assert role_typed_claim_guard("ghost-role", "code") is None


def test_role_typed_claim_guard_developer_can_claim_code() -> None:
    assert role_typed_claim_guard("developer", "code") is None


def test_role_typed_claim_guard_developer_cannot_claim_review() -> None:
    env = role_typed_claim_guard("developer", "review")
    assert env is not None


def test_sibling_sequence_guard_root_task_passes() -> None:
    """Line 152-153: parent_task_id None → no guard."""
    task = SimpleNamespace(id=uuid4(), parent_task_id=None, sequence=5)
    assert sibling_sequence_guard(task, []) is None


def test_sibling_sequence_guard_sequence_zero_passes() -> None:
    """Line 156: sequence==0 always allowed."""
    task = SimpleNamespace(id=uuid4(), parent_task_id=uuid4(), sequence=0)
    assert sibling_sequence_guard(task, []) is None


def test_sibling_sequence_guard_blocks_when_earlier_sibling_open() -> None:
    parent = uuid4()
    target = SimpleNamespace(id=uuid4(), parent_task_id=parent, sequence=2)
    earlier = SimpleNamespace(
        id=uuid4(), parent_task_id=parent, sequence=1, status="in_progress"
    )
    env = sibling_sequence_guard(target, [earlier])
    assert env is not None


def test_sibling_sequence_guard_passes_when_earlier_sibling_terminal() -> None:
    parent = uuid4()
    target = SimpleNamespace(id=uuid4(), parent_task_id=parent, sequence=2)
    earlier = SimpleNamespace(
        id=uuid4(), parent_task_id=parent, sequence=1, status="completed"
    )
    assert sibling_sequence_guard(target, [earlier]) is None
