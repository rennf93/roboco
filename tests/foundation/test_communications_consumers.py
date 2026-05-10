"""Verify communications-policy consumers derive from foundation."""

from __future__ import annotations

import ast
from pathlib import Path

from roboco.agents_config import CHANNEL_ACCESS
from roboco.foundation.identity import AGENTS, Role, Team
from roboco.foundation.policy import communications
from roboco.foundation.policy.communications import ChannelSpec
from roboco.seeds.initial_data import (
    AUDITOR_SILENT_ACCESS,
    CHANNEL_MEMBERSHIPS,
    DEFAULT_CHANNELS,
)
from roboco.services.gateway import content_actions

# Cell-member roles that are subject to a channel's team_scope. Cross-cell
# roles (MAIN_PM, AUDITOR, CEO, board) are NOT filtered — they participate
# regardless of which team a cell channel scopes to.
_TEAM_SCOPED_ROLES: frozenset[Role] = frozenset(
    {Role.DEVELOPER, Role.QA, Role.DOCUMENTER, Role.CELL_PM}
)


def _expected_slugs(role_set: frozenset[Role], team_scope: Team | None) -> set[str]:
    """Expand a role-set to slugs, honoring an optional team_scope.

    A slug qualifies when its role is in `role_set` AND, if the role is in
    _TEAM_SCOPED_ROLES and team_scope is set, its team matches team_scope.
    System sentinel is always excluded.
    """
    out: set[str] = set()
    for slug, row in AGENTS.items():
        if slug == "system":
            continue
        if row.role not in role_set:
            continue
        if (
            team_scope is not None
            and row.role in _TEAM_SCOPED_ROLES
            and row.team != team_scope
        ):
            continue
        out.add(slug)
    return out


def test_agents_config_channel_access_keys_match_foundation_channels() -> None:
    """Every channel slug in CHANNEL_ACCESS is present in CHANNELS."""
    legacy_slugs = set(CHANNEL_ACCESS.keys())
    foundation_slugs = set(communications.CHANNELS.keys())
    assert legacy_slugs == foundation_slugs, (
        f"slug drift: legacy_only={legacy_slugs - foundation_slugs}, "
        f"foundation_only={foundation_slugs - legacy_slugs}"
    )


def test_channel_access_read_membership_derives_from_foundation_role_to_slug() -> None:
    """CHANNEL_ACCESS[slug]['read'] == every agent slug whose role is in
    foundation.CHANNELS[slug].read_roles minus silent_roles, filtered by
    team_scope when set. Legacy semantics keep silent observers in the
    'silent' bucket and out of 'read'; the access check treats them as
    read-allowed at runtime."""
    for slug, spec in communications.CHANNELS.items():
        cfg_read = set(CHANNEL_ACCESS[slug]["read"])
        active_read_roles = spec.read_roles - spec.silent_roles
        expected_read = _expected_slugs(active_read_roles, spec.team_scope)
        assert cfg_read == expected_read, (
            f"{slug} read drift: cfg={sorted(cfg_read)} "
            f"expected={sorted(expected_read)}"
        )


def test_channel_access_write_membership_derives_from_foundation() -> None:
    """Same for write_roles."""
    for slug, spec in communications.CHANNELS.items():
        cfg_write = set(CHANNEL_ACCESS[slug]["write"])
        expected_write = _expected_slugs(spec.write_roles, spec.team_scope)
        assert cfg_write == expected_write, (
            f"{slug} write drift: cfg={sorted(cfg_write)} "
            f"expected={sorted(expected_write)}"
        )


def test_channel_access_silent_membership_derives_from_foundation() -> None:
    """CHANNEL_ACCESS[slug]['silent'] == slugs derived from silent_roles."""
    for slug, spec in communications.CHANNELS.items():
        cfg_silent = set(CHANNEL_ACCESS[slug]["silent"])
        expected_silent = _expected_slugs(spec.silent_roles, spec.team_scope)
        assert cfg_silent == expected_silent, (
            f"{slug} silent drift: cfg={sorted(cfg_silent)} "
            f"expected={sorted(expected_silent)}"
        )


def test_channelspec_dataclass_exposes_team_scope() -> None:
    """ChannelSpec must carry team_scope so cell channels can scope membership."""
    fields = {f.name for f in ChannelSpec.__dataclass_fields__.values()}
    assert "team_scope" in fields


def test_seed_default_channels_match_foundation_slugs() -> None:
    seed_slugs = {ch["slug"] for ch in DEFAULT_CHANNELS}
    foundation_slugs = set(communications.CHANNELS)
    assert seed_slugs == foundation_slugs, (
        f"seed/foundation slug drift: {seed_slugs ^ foundation_slugs}"
    )


def test_seed_default_channels_descriptions_match_foundation() -> None:
    """Description text comes from the foundation ChannelSpec.description."""
    by_slug = {ch["slug"]: ch for ch in DEFAULT_CHANNELS}
    for slug, spec in communications.CHANNELS.items():
        assert by_slug[slug].get("description") == spec.description, (
            f"{slug} description drift: "
            f"seed={by_slug[slug].get('description')!r} "
            f"foundation={spec.description!r}"
        )


def test_channel_memberships_derives_from_foundation_role_to_slug() -> None:
    """CHANNEL_MEMBERSHIPS[slug] == sorted slugs whose role is in
    CHANNELS[slug].read_roles, filtered by team_scope when set."""
    for slug, spec in communications.CHANNELS.items():
        seed_members = set(CHANNEL_MEMBERSHIPS.get(slug, []))
        team_scope = getattr(spec, "team_scope", None)
        expected = _expected_slugs(spec.read_roles, team_scope)
        assert seed_members == expected, (
            f"{slug} membership drift: seed={sorted(seed_members)} "
            f"expected={sorted(expected)}"
        )


def test_auditor_silent_access_derives_from_foundation() -> None:
    """AUDITOR_SILENT_ACCESS == channels where AUDITOR is in silent_roles."""
    expected = {
        slug
        for slug, spec in communications.CHANNELS.items()
        if Role.AUDITOR in spec.silent_roles
    }
    assert set(AUDITOR_SILENT_ACCESS) == expected, (
        f"auditor silent drift: seed={sorted(AUDITOR_SILENT_ACCESS)} "
        f"expected={sorted(expected)}"
    )


def test_content_actions_notify_allowed_roles_matches_foundation() -> None:
    cfg_set = {
        r if isinstance(r, str) else r.value
        for r in content_actions._NOTIFY_ALLOWED_ROLES
    }
    foundation_set = {r.value for r in communications.NOTIFY_SENDER_ROLES}
    assert cfg_set == foundation_set, (
        f"_NOTIFY_ALLOWED_ROLES drift: cfg={cfg_set} foundation={foundation_set}"
    )


def test_content_actions_valid_priorities_matches_foundation() -> None:
    cfg = set(content_actions._VALID_NOTIFY_PRIORITIES)
    foundation = {p.value for p in communications.Priority}
    assert cfg == foundation


def test_notification_delivery_uses_ack_required_table() -> None:
    """All NotificationTable() construction sites must source `requires_ack`
    from ACK_REQUIRED_BY_TYPE, not from a hand-set boolean literal."""
    src = Path("roboco/services/notification_delivery.py").read_text()
    tree = ast.parse(src)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        callee_name = (
            callee.attr
            if isinstance(callee, ast.Attribute)
            else callee.id
            if isinstance(callee, ast.Name)
            else None
        )
        if callee_name != "NotificationTable":
            continue
        for kw in node.keywords:
            if kw.arg != "requires_ack":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool):
                offenders.append(
                    f"line {kw.value.lineno}: requires_ack={kw.value.value}"
                )
    assert offenders == [], (
        "hand-set requires_ack literals remain in notification_delivery.py: "
        f"{offenders}"
    )
