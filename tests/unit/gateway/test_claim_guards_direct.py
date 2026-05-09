"""Direct unit tests for claim_guards helpers (branches only)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from roboco.services.gateway.claim_guards import sibling_sequence_guard


def test_sibling_sequence_guard_root_task_passes() -> None:
    """parent_task_id None → no guard."""
    task = SimpleNamespace(id=uuid4(), parent_task_id=None, sequence=5)
    assert sibling_sequence_guard(task, []) is None


def test_sibling_sequence_guard_sequence_zero_passes() -> None:
    """sequence==0 always allowed."""
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
