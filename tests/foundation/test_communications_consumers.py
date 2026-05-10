"""Verify communications-policy consumers derive from foundation."""

from __future__ import annotations

from roboco.agents_config import CHANNEL_ACCESS
from roboco.foundation.identity import AGENTS, Role, Team
from roboco.foundation.policy import communications
from roboco.foundation.policy.communications import ChannelSpec

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
