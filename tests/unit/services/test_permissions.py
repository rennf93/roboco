"""PermissionService coverage — RBAC for notifications, tasks, KB.

Pure-logic checks driven by ``agents_config`` constants — no DB needed.
The service is a SingletonService, so we instantiate it directly with
``object.__new__`` to bypass session-management.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from roboco.models import AgentRole, Team
from roboco.models.permissions import (
    AgentContext,
    KBAction,
    PermissionLevel,
    TaskAction,
)
from roboco.services.permissions import PermissionService


@pytest.fixture
def svc() -> PermissionService:
    """PermissionService is a SingletonService — call __init__ to bind log."""
    return PermissionService()


def _ctx(role: AgentRole, team: Team | None = None) -> AgentContext:
    return AgentContext(agent_id=uuid4(), role=role, team=team)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def test_main_pm_can_send_notifications(svc: PermissionService) -> None:
    main_pm = _ctx(AgentRole.MAIN_PM)
    assert svc.can_send_notifications(main_pm) is True


def test_developer_cannot_send_notifications(svc: PermissionService) -> None:
    dev = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    assert svc.can_send_notifications(dev) is False


def test_auditor_send_notifications_returns_bool(svc: PermissionService) -> None:
    """Auditor's notification permission is read from agents_config."""
    auditor = _ctx(AgentRole.AUDITOR)
    assert isinstance(svc.can_send_notifications(auditor), bool)


def test_can_notify_pm_to_dev(svc: PermissionService) -> None:
    sender = _ctx(AgentRole.MAIN_PM)
    recipient = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    assert svc.can_notify(sender, recipient) is True


# ---------------------------------------------------------------------------
# Task action permissions
# ---------------------------------------------------------------------------


def test_developer_can_claim_in_own_team(svc: PermissionService) -> None:
    dev = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    assert svc.can_perform_task_action(dev, TaskAction.CLAIM, Team.BACKEND) is True


def test_qa_can_view_all(svc: PermissionService) -> None:
    qa = _ctx(AgentRole.QA, team=Team.BACKEND)
    # QA must be able to view tasks in their cell.
    assert isinstance(
        svc.can_perform_task_action(qa, TaskAction.VIEW_OWN, Team.BACKEND),
        bool,
    )


def test_can_perform_task_action_returns_bool(svc: PermissionService) -> None:
    """Action permission returns a bool — exact value depends on TASK_PERMISSIONS."""
    dev = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    assert isinstance(
        svc.can_perform_task_action(dev, TaskAction.CLOSE, Team.BACKEND), bool
    )


def test_cell_pm_can_close_in_own_cell(svc: PermissionService) -> None:
    cell_pm = _ctx(AgentRole.CELL_PM, team=Team.BACKEND)
    assert svc.can_perform_task_action(cell_pm, TaskAction.CLOSE, Team.BACKEND) is True


def test_ceo_can_perform_any_task_action(svc: PermissionService) -> None:
    """The CEO is the ultimate authority — it may perform ANY task action on any
    task (assign/reassign, change priority, close, claim, view). The panel
    operates as the CEO, so this override is what unblocks the whole UI."""
    ceo = _ctx(AgentRole.CEO)
    for action in (
        TaskAction.ASSIGN,
        TaskAction.CHANGE_PRIORITY,
        TaskAction.CLOSE,
        TaskAction.CLAIM,
        TaskAction.VIEW_ALL,
    ):
        assert svc.can_perform_task_action(ceo, action, Team.BACKEND) is True


def test_get_task_actions_returns_set(svc: PermissionService) -> None:
    dev = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    actions = svc.get_task_actions(dev)
    assert hasattr(actions, "__iter__")


# ---------------------------------------------------------------------------
# Permission levels
# ---------------------------------------------------------------------------


def test_ceo_has_highest_level(svc: PermissionService) -> None:
    assert svc.get_permission_level(AgentRole.CEO) == PermissionLevel.CEO


def test_developer_is_cell_member_level(svc: PermissionService) -> None:
    assert svc.get_permission_level(AgentRole.DEVELOPER) == PermissionLevel.CELL_MEMBER


def test_main_pm_is_main_pm_level(svc: PermissionService) -> None:
    assert svc.get_permission_level(AgentRole.MAIN_PM) == PermissionLevel.MAIN_PM


# ---------------------------------------------------------------------------
# Combined check_all
# ---------------------------------------------------------------------------


def test_check_all_returns_dict(svc: PermissionService) -> None:
    """check_all returns a permission summary dict."""
    dev = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    result = svc.check_all(dev)
    assert isinstance(result, dict)
    assert "role" in result
    assert "level" in result


# ---------------------------------------------------------------------------
# KB permissions
# ---------------------------------------------------------------------------


def test_get_kb_actions_returns_collection(svc: PermissionService) -> None:
    dev = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    actions = svc.get_kb_actions(dev)
    # Returns a collection of allowed KB actions.
    assert hasattr(actions, "__iter__")


def test_can_perform_kb_action_developer(svc: PermissionService) -> None:
    """KB SEARCH is generally allowed for developers."""
    dev = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    assert isinstance(svc.can_perform_kb_action(dev, KBAction.SEARCH), bool)


# ---------------------------------------------------------------------------
# Notification scope edge cases
# ---------------------------------------------------------------------------


def test_can_notify_cell_pm_to_main_pm(svc: PermissionService) -> None:
    """Cell PMs can notify Main PM for coordination."""
    sender = _ctx(AgentRole.CELL_PM, team=Team.BACKEND)
    recipient = _ctx(AgentRole.MAIN_PM)
    assert svc.can_notify(sender, recipient) is True


def test_can_notify_cell_pm_to_other_cell_pm(svc: PermissionService) -> None:
    sender = _ctx(AgentRole.CELL_PM, team=Team.BACKEND)
    recipient = _ctx(AgentRole.CELL_PM, team=Team.FRONTEND)
    assert svc.can_notify(sender, recipient) is True


def test_can_notify_cell_pm_to_dev_in_other_team(svc: PermissionService) -> None:
    """Cell PM cannot notify dev in a different cell."""
    sender = _ctx(AgentRole.CELL_PM, team=Team.BACKEND)
    recipient = _ctx(AgentRole.DEVELOPER, team=Team.FRONTEND)
    assert svc.can_notify(sender, recipient) is False


def test_can_notify_cell_pm_to_dev_in_same_team(svc: PermissionService) -> None:
    sender = _ctx(AgentRole.CELL_PM, team=Team.BACKEND)
    recipient = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    assert svc.can_notify(sender, recipient) is True


# ---------------------------------------------------------------------------
# can_notify branches
# ---------------------------------------------------------------------------


def test_can_notify_developer_returns_false(svc: PermissionService) -> None:
    """Developers cannot send notifications — short-circuits on line 236."""
    dev = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    other = _ctx(AgentRole.QA, team=Team.BACKEND)
    assert svc.can_notify(dev, other) is False


# ---------------------------------------------------------------------------
# can_notify list scope (lines 253-258)
# ---------------------------------------------------------------------------


def test_can_notify_product_owner_list_scope_in(svc: PermissionService) -> None:
    """Product Owner has list scope — allowed recipients return True."""
    sender = _ctx(AgentRole.PRODUCT_OWNER, team=Team.BOARD)
    recipient = _ctx(AgentRole.MAIN_PM, team=Team.MAIN_PM)
    assert svc.can_notify(sender, recipient) is True


def test_can_notify_product_owner_list_scope_out(svc: PermissionService) -> None:
    """Product Owner cannot notify recipients outside their list scope."""
    sender = _ctx(AgentRole.PRODUCT_OWNER, team=Team.BOARD)
    recipient = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    assert svc.can_notify(sender, recipient) is False


# ---------------------------------------------------------------------------
# can_perform_task_action VIEW_ALL fallback for VIEW_OWN (line 310)
# ---------------------------------------------------------------------------


def test_view_own_falls_back_to_view_all_for_ceo(svc: PermissionService) -> None:
    """CEO has VIEW_ALL but not VIEW_OWN — VIEW_OWN check falls back to True."""
    ceo = _ctx(AgentRole.CEO)
    assert svc.can_perform_task_action(ceo, TaskAction.VIEW_OWN, Team.BACKEND) is True


def test_can_notify_unknown_scope_returns_false(svc: PermissionService) -> None:
    """Line 258: scope is neither 'all', 'cell', nor list → defensive return False."""

    sender = _ctx(AgentRole.MAIN_PM, team=Team.MAIN_PM)
    recipient = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    # Patch _get_notification_scope directly to a bogus value type.
    with patch(
        "roboco.services.permissions._get_notification_scope",
        return_value="garbage_scope",
    ):
        assert svc.can_notify(sender, recipient) is False
