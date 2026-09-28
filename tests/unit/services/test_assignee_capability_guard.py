"""Unit tests for TaskService._refuse_incapable_assignee — the assignment-path
capability backstop (delegate / reassign / admin set).
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.identity import AGENTS
from roboco.models.base import TaskStatus
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services.base import AssigneeCapabilityError
from roboco.services.task import TaskService


def _svc() -> TaskService:
    return TaskService(MagicMock())


def _agent_uuid(role_value: str) -> object:
    for row in AGENTS.values():
        if row.role.value == role_value:
            return row.uuid
    raise AssertionError(f"no seeded agent with role {role_value}")


def test_guard_accepts_capable_assignee() -> None:
    svc = _svc()
    # Must not raise.
    svc._refuse_incapable_assignee(TaskStatus.PENDING, _agent_uuid("developer"))


def test_guard_accepts_null_and_unknown_assignee() -> None:
    svc = _svc()
    svc._refuse_incapable_assignee(TaskStatus.PENDING, None)
    # A non-seeded UUID has no resolvable role — existing identity guards own it.
    svc._refuse_incapable_assignee(TaskStatus.PENDING, uuid4())


def test_guard_refuses_board_role_on_delivery_task() -> None:
    """The 54e30535 wedge shape: a product-owner id on a pending task."""
    svc = _svc()
    with pytest.raises(AssigneeCapabilityError) as excinfo:
        svc._refuse_incapable_assignee(TaskStatus.PENDING, _agent_uuid("product_owner"))
    assert "product_owner" in str(excinfo.value.message)
    assert "pending" in str(excinfo.value.message)


@pytest.mark.parametrize(
    ("role_value", "status"),
    [
        ("product_owner", TaskStatus.IN_PROGRESS),
        ("auditor", TaskStatus.NEEDS_REVISION),
        ("head_marketing", TaskStatus.PENDING),
        ("developer", TaskStatus.AWAITING_QA),
        ("qa", TaskStatus.PENDING),
    ],
)
def test_guard_refuses_cross_lane_assignments(
    role_value: str, status: TaskStatus
) -> None:
    svc = _svc()
    with pytest.raises(AssigneeCapabilityError):
        svc._refuse_incapable_assignee(status, _agent_uuid(role_value))


def test_guard_accepts_terminal_status_for_any_role() -> None:
    svc = _svc()
    svc._refuse_incapable_assignee(TaskStatus.COMPLETED, _agent_uuid("product_owner"))


def test_seeded_uuid_lookup_resolves_every_seeded_role() -> None:
    """Sanity: the AGENT_UUIDS/AGENTS import surface used above is complete."""
    for role_value in ("developer", "product_owner", "cell_pm", "qa"):
        assert _agent_uuid(role_value) is not None
    assert AGENT_UUIDS  # the slug->uuid map stays populated
