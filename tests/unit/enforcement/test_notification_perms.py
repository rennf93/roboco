"""enforcement.notification_perms coverage."""

from __future__ import annotations

import pytest
from roboco.enforcement.notification_perms import (
    NotificationPermissionError,
    get_notification_scope,
    validate_notification_permission,
)


def test_developer_cannot_send_notifications() -> None:
    with pytest.raises(NotificationPermissionError, match="cannot send"):
        validate_notification_permission("be-dev-1", ["be-pm"])


def test_main_pm_can_send_to_anyone() -> None:
    assert validate_notification_permission("main-pm", ["be-dev-1"]) is True


def test_cell_pm_can_notify_cell_member() -> None:
    assert validate_notification_permission("be-pm", ["be-dev-1"]) is True


def test_cell_pm_can_notify_other_cell_pm() -> None:
    assert validate_notification_permission("be-pm", ["fe-pm"]) is True


def test_cell_pm_can_notify_main_pm() -> None:
    assert validate_notification_permission("be-pm", ["main-pm"]) is True


def test_cell_pm_cannot_notify_other_cell_dev() -> None:
    with pytest.raises(NotificationPermissionError):
        validate_notification_permission("be-pm", ["fe-dev-1"])


def test_get_notification_scope_for_main_pm() -> None:
    scope = get_notification_scope("main-pm")
    assert scope.get("can_send") is True


def test_get_notification_scope_for_developer() -> None:
    scope = get_notification_scope("be-dev-1")
    assert scope.get("can_send") is False


def test_get_notification_scope_for_unknown() -> None:
    scope = get_notification_scope("ghost-agent")
    assert scope.get("can_send") is False


def test_validate_with_multiple_recipients() -> None:
    """Validate succeeds when all recipients are reachable."""
    assert validate_notification_permission("main-pm", ["be-dev-1", "fe-dev-1"]) is True


def test_validate_fails_on_first_unreachable() -> None:
    """Validation halts at the first unreachable recipient."""
    with pytest.raises(NotificationPermissionError):
        validate_notification_permission("be-pm", ["be-dev-1", "fe-dev-1"])


def test_unknown_agent_cannot_send() -> None:
    with pytest.raises(NotificationPermissionError):
        validate_notification_permission("ghost-agent", ["be-pm"])
