"""Tier 1 — communications policy: Priority + sender allowlist + ack-required."""

from __future__ import annotations

from roboco.foundation import identity
from roboco.foundation.policy import communications
from roboco.models.base import NotificationPriority, NotificationType


def test_priority_enum_matches_notification_priority() -> None:
    """Communications.Priority is a re-export of NotificationPriority."""
    assert communications.Priority is NotificationPriority


def test_notify_sender_roles_includes_pms_and_board_and_ceo() -> None:
    expected = frozenset(
        {
            identity.Role.CELL_PM,
            identity.Role.MAIN_PM,
            identity.Role.PRODUCT_OWNER,
            identity.Role.HEAD_MARKETING,
            identity.Role.CEO,
        }
    )
    assert expected == communications.NOTIFY_SENDER_ROLES


def test_notify_sender_roles_excludes_auditor() -> None:
    """Auditor is silent — no notification sending."""
    assert identity.Role.AUDITOR not in communications.NOTIFY_SENDER_ROLES


def test_ack_required_by_type_covers_every_notification_type() -> None:
    for nt in NotificationType:
        assert nt in communications.ACK_REQUIRED_BY_TYPE, (
            f"{nt.value} missing from ACK_REQUIRED_BY_TYPE"
        )


def test_ack_required_for_blocker_escalation() -> None:
    assert (
        communications.ACK_REQUIRED_BY_TYPE[NotificationType.BLOCKER_ESCALATION] is True
    )


def test_ack_not_required_for_task_assignment() -> None:
    assert (
        communications.ACK_REQUIRED_BY_TYPE[NotificationType.TASK_ASSIGNMENT] is False
    )
