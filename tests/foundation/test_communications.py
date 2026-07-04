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


# ----- parse_priority -------------------------------------------------------


def test_parse_priority_recognizes_string_normal() -> None:
    assert communications.parse_priority("normal") is communications.Priority.NORMAL


def test_parse_priority_recognizes_string_high() -> None:
    assert communications.parse_priority("high") is communications.Priority.HIGH


def test_parse_priority_recognizes_string_urgent() -> None:
    assert communications.parse_priority("urgent") is communications.Priority.URGENT


def test_parse_priority_unknown_string_falls_back_to_normal() -> None:
    assert (
        communications.parse_priority("definitely-not-real")
        is communications.Priority.NORMAL
    )


def test_parse_priority_legacy_urgent_flag_maps_to_urgent() -> None:
    assert (
        communications.parse_priority(None, legacy_urgent_flag=True)
        is communications.Priority.URGENT
    )


def test_parse_priority_default_is_normal() -> None:
    assert communications.parse_priority(None) is communications.Priority.NORMAL


def test_parse_priority_explicit_priority_wins_over_legacy_flag() -> None:
    """Spec §5.5 precedence: priority string beats legacy urgent bool."""
    assert (
        communications.parse_priority("normal", legacy_urgent_flag=True)
        is communications.Priority.NORMAL
    )
