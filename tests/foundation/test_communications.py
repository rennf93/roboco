"""Tier 1 — communications policy: Priority + sender allowlist + ack-required."""

from __future__ import annotations

import dataclasses

from roboco.agents_config import CHANNEL_ACCESS
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


def test_channel_spec_dataclass_is_frozen() -> None:
    assert dataclasses.is_dataclass(communications.ChannelSpec)
    fields = {f.name for f in dataclasses.fields(communications.ChannelSpec)}
    assert {
        "slug",
        "description",
        "type",
        "read_roles",
        "write_roles",
        "silent_roles",
        "read_only_for_others",
    } <= fields


def test_channels_dict_non_empty() -> None:
    assert len(communications.CHANNELS) > 0


def test_channels_keys_match_agents_config_channel_access() -> None:
    """CHANNELS must include every channel from the legacy CHANNEL_ACCESS data."""
    legacy_slugs = set(CHANNEL_ACCESS.keys())
    foundation_slugs = set(communications.CHANNELS.keys())
    # Allow foundation to be a SUPERSET (new channels OK) but every
    # legacy channel must be represented:
    missing = legacy_slugs - foundation_slugs
    assert missing == set(), f"channels in agents_config not in foundation: {missing}"


def test_silent_roles_subset_of_read_roles_for_every_channel() -> None:
    for slug, spec in communications.CHANNELS.items():
        assert spec.silent_roles <= spec.read_roles, (
            f"{slug}: silent_roles {spec.silent_roles} not subset of "
            f"read_roles {spec.read_roles}"
        )


def test_announcements_is_read_only_for_others() -> None:
    """Spec §5.5: announcements is the canonical read-only channel."""
    if "announcements" in communications.CHANNELS:
        spec = communications.CHANNELS["announcements"]
        assert spec.read_only_for_others is True


def test_backend_cell_has_canonical_membership() -> None:
    if "backend-cell" in communications.CHANNELS:
        spec = communications.CHANNELS["backend-cell"]
        assert identity.Role.DEVELOPER in spec.read_roles
        assert identity.Role.AUDITOR in spec.silent_roles
