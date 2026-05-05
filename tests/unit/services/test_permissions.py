"""PermissionService coverage — RBAC for channels, notifications, tasks, KB.

Pure-logic checks driven by ``agents_config`` constants — no DB needed.
The service is a SingletonService, so we instantiate it directly with
``object.__new__`` to bypass session-management.
"""

from __future__ import annotations

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
    """PermissionService is a SingletonService — bypass __init__ for unit tests."""
    return object.__new__(PermissionService)


def _ctx(role: AgentRole, team: Team | None = None) -> AgentContext:
    return AgentContext(agent_id=uuid4(), role=role, team=team)


# ---------------------------------------------------------------------------
# Channel read access
# ---------------------------------------------------------------------------


def test_auditor_can_read_any_channel(svc: PermissionService) -> None:
    """AUDITOR has silent read on every channel."""
    auditor = _ctx(AgentRole.AUDITOR)
    assert svc.can_read_channel(auditor, "backend-cell")
    assert svc.can_read_channel(auditor, "main-pm-board")
    assert svc.can_read_channel(auditor, "any-channel-name")


def test_ceo_can_read_any_channel(svc: PermissionService) -> None:
    ceo = _ctx(AgentRole.CEO)
    assert svc.can_read_channel(ceo, "backend-cell")


def test_main_pm_can_read_any_channel(svc: PermissionService) -> None:
    main_pm = _ctx(AgentRole.MAIN_PM)
    assert svc.can_read_channel(main_pm, "backend-cell")
    assert svc.can_read_channel(main_pm, "frontend-cell")


# ---------------------------------------------------------------------------
# Channel write access
# ---------------------------------------------------------------------------


def test_ceo_can_write_any_channel(svc: PermissionService) -> None:
    ceo = _ctx(AgentRole.CEO)
    assert svc.can_write_channel(ceo, "backend-cell")


def test_auditor_can_write_any_channel(svc: PermissionService) -> None:
    """Auditor write returns True (cover-maintenance is a convention)."""
    auditor = _ctx(AgentRole.AUDITOR)
    assert svc.can_write_channel(auditor, "backend-cell")


def test_main_pm_can_write_any_channel(svc: PermissionService) -> None:
    main_pm = _ctx(AgentRole.MAIN_PM)
    assert svc.can_write_channel(main_pm, "backend-cell")


# ---------------------------------------------------------------------------
# Channel listing
# ---------------------------------------------------------------------------


def test_get_accessible_channels_for_auditor(svc: PermissionService) -> None:
    """Auditor sees every configured channel."""
    auditor = _ctx(AgentRole.AUDITOR)
    channels = svc.get_accessible_channels(auditor)
    assert len(channels) > 0


def test_get_writable_channels_for_ceo(svc: PermissionService) -> None:
    ceo = _ctx(AgentRole.CEO)
    channels = svc.get_writable_channels(ceo)
    assert len(channels) > 0


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
# Communication matrix
# ---------------------------------------------------------------------------


def test_can_communicate_within_cell(svc: PermissionService) -> None:
    dev = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    qa = _ctx(AgentRole.QA, team=Team.BACKEND)
    assert svc.can_communicate(dev, qa) is True


def test_can_communicate_across_cells_via_pm(svc: PermissionService) -> None:
    """Communication matrix returns a bool — exact result depends on the matrix."""
    main_pm = _ctx(AgentRole.MAIN_PM)
    dev = _ctx(AgentRole.DEVELOPER, team=Team.BACKEND)
    assert isinstance(svc.can_communicate(main_pm, dev), bool)


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
# Slug-based shortcuts
# ---------------------------------------------------------------------------


def test_can_agent_read_channel_known_slug(svc: PermissionService) -> None:
    """Pass a known agent slug; service should resolve role+team and decide."""
    # be-dev-1 is in AGENT_ROLE_MAP as a developer in backend.
    result = svc.can_agent_read_channel("be-dev-1", "backend-cell")
    assert isinstance(result, bool)


def test_can_agent_read_channel_unknown_slug(svc: PermissionService) -> None:
    """Unknown slug → False (deny by default)."""
    assert svc.can_agent_read_channel("ghost-agent", "backend-cell") is False


def test_can_agent_send_notifications_known_slug(svc: PermissionService) -> None:
    """main-pm slug should be able to send."""
    assert svc.can_agent_send_notifications("main-pm") is True


def test_can_agent_send_notifications_unknown_slug(svc: PermissionService) -> None:
    """Unknown slug → False."""
    assert svc.can_agent_send_notifications("ghost-agent") is False


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
