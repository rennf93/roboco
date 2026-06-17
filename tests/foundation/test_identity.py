"""Tier 1 — identity self-tests. Fast (no DB, no network)."""

from __future__ import annotations

from enum import IntEnum

import pytest
from roboco.foundation import identity
from roboco.seeds.initial_data import AGENT_UUIDS


def test_role_enum_has_every_role_inc_system() -> None:
    """Every role the system uses must be enumerated, including SYSTEM."""
    expected = {
        "developer",
        "qa",
        "documenter",
        "cell_pm",
        "main_pm",
        "product_owner",
        "head_marketing",
        "auditor",
        "pr_reviewer",
        "prompter",
        "secretary",
        "ceo",
        "system",
    }
    actual = {r.value for r in identity.Role}
    assert actual == expected, f"Role drift: {actual ^ expected}"


def test_team_enum_has_marketing_legacy_and_system() -> None:
    """Team enum keeps MARKETING for legacy seed-data parity; SYSTEM for sentinel."""
    expected = {
        "backend",
        "frontend",
        "ux_ui",
        "board",
        "main_pm",
        "fullstack",
        "marketing",  # legacy — see spec §5.1
        "system",
    }
    actual = {t.value for t in identity.Team}
    assert actual == expected, f"Team drift: {actual ^ expected}"


def test_role_level_is_int_enum() -> None:
    """RoleLevel is hierarchical (orderable), not a stringly-typed set."""
    assert issubclass(identity.RoleLevel, IntEnum)
    # CEO > everyone else
    assert identity.RoleLevel.CEO > identity.RoleLevel.AUDITOR
    assert identity.RoleLevel.AUDITOR > identity.RoleLevel.MAIN_PM
    assert identity.RoleLevel.MAIN_PM > identity.RoleLevel.CELL_PM
    assert identity.RoleLevel.CELL_PM > identity.RoleLevel.DOCUMENTER
    assert identity.RoleLevel.DOCUMENTER > identity.RoleLevel.QA
    assert identity.RoleLevel.QA > identity.RoleLevel.DEV
    assert identity.RoleLevel.DEV > identity.RoleLevel.SYSTEM


def test_agents_catalog_has_all_seed_slugs() -> None:
    """Every slug from seeds/initial_data.AGENT_UUIDS is in foundation.AGENTS."""
    expected_slugs = {
        "system",
        "ceo",
        "be-dev-1",
        "be-dev-2",
        "be-qa",
        "be-pm",
        "be-doc",
        "fe-dev-1",
        "fe-dev-2",
        "fe-qa",
        "fe-pm",
        "fe-doc",
        "ux-dev-1",
        "ux-dev-2",
        "ux-qa",
        "ux-pm",
        "ux-doc",
        "main-pm",
        "product-owner",
        "head-marketing",
        "auditor",
        "intake-1",
        "secretary-1",
        "pr-reviewer-1",
    }
    actual = set(identity.AGENTS.keys())
    assert actual == expected_slugs, f"agent catalog drift: {actual ^ expected_slugs}"


def test_agents_uuids_match_seed() -> None:
    """UUIDs match seeds/initial_data.AGENT_UUIDS (the authoritative seed map)."""
    for slug, expected_uuid_str in AGENT_UUIDS.items():
        assert str(identity.AGENTS[slug].uuid) == expected_uuid_str, (
            f"UUID drift for {slug!r}: foundation says "
            f"{identity.AGENTS[slug].uuid}, seed says {expected_uuid_str}"
        )


def test_ceo_is_human() -> None:
    """CEO is the only is_human=True row."""
    humans = {slug for slug, row in identity.AGENTS.items() if row.is_human}
    assert humans == {"ceo"}, f"unexpected human flag: {humans}"


def test_head_marketing_team_is_board() -> None:
    """Resolves the head-marketing.team drift (spec §5.1)."""
    assert identity.AGENTS["head-marketing"].team == identity.Team.BOARD


def test_no_agent_declares_marketing_team() -> None:
    """Team.MARKETING exists for legacy parity; no agent should claim it."""
    using_marketing = [
        slug
        for slug, row in identity.AGENTS.items()
        if row.team == identity.Team.MARKETING
    ]
    assert using_marketing == [], (
        f"agents claiming Team.MARKETING (legacy): {using_marketing}"
    )


def test_pm_roles_is_canonical() -> None:
    """PM_ROLES is exactly {CELL_PM, MAIN_PM} — replaces both forked variants."""
    assert (
        frozenset({identity.Role.CELL_PM, identity.Role.MAIN_PM}) == identity.PM_ROLES
    )


def test_board_roles_includes_auditor() -> None:
    """BOARD_ROLES is the strategic layer (PO + Head Marketing + Auditor)."""
    assert (
        frozenset(
            {
                identity.Role.PRODUCT_OWNER,
                identity.Role.HEAD_MARKETING,
                identity.Role.AUDITOR,
            }
        )
        == identity.BOARD_ROLES
    )


def test_dev_roles_has_developer_only() -> None:
    """DEV_ROLES intentionally narrow — devs only, no QA/Doc."""
    assert frozenset({identity.Role.DEVELOPER}) == identity.DEV_ROLES


def test_all_roles_covers_enum() -> None:
    """ALL_ROLES matches the Role enum exactly."""
    assert frozenset(identity.Role) == identity.ALL_ROLES


def test_role_level_covers_every_role() -> None:
    """Every Role has a RoleLevel. SYSTEM is the sentinel (lowest)."""
    for role in identity.Role:
        assert role in identity.ROLE_LEVEL, f"Role.{role.name} missing from ROLE_LEVEL"
    assert identity.ROLE_LEVEL[identity.Role.SYSTEM] == identity.RoleLevel.SYSTEM
    assert identity.ROLE_LEVEL[identity.Role.CEO] == identity.RoleLevel.CEO


def test_role_level_orders_correctly() -> None:
    """CEO > AUDITOR > BOARD > MAIN_PM > CELL_PM > DOC > QA > DEV > SYSTEM."""
    levels = [
        identity.ROLE_LEVEL[r]
        for r in (
            identity.Role.CEO,
            identity.Role.AUDITOR,
            identity.Role.PRODUCT_OWNER,  # BOARD level
            identity.Role.MAIN_PM,
            identity.Role.CELL_PM,
            identity.Role.DOCUMENTER,
            identity.Role.QA,
            identity.Role.DEVELOPER,
            identity.Role.SYSTEM,
        )
    ]
    assert levels == sorted(levels, reverse=True)


def test_agent_for_slug_returns_row() -> None:
    row = identity.agent_for_slug("be-dev-1")
    assert row.slug == "be-dev-1"
    assert row.role == identity.Role.DEVELOPER
    assert row.team == identity.Team.BACKEND


def test_agent_for_slug_unknown_raises_key_error() -> None:
    with pytest.raises(KeyError) as exc_info:
        identity.agent_for_slug("notreal-1")
    assert "notreal-1" in str(exc_info.value)


def test_slugs_for_role_developer() -> None:
    devs = identity.slugs_for_role(identity.Role.DEVELOPER)
    assert devs == frozenset(
        {"be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev-1", "ux-dev-2"}
    )


def test_slugs_for_role_system_returns_singleton() -> None:
    assert identity.slugs_for_role(identity.Role.SYSTEM) == frozenset({"system"})


def test_slugs_for_team_backend() -> None:
    backend = identity.slugs_for_team(identity.Team.BACKEND)
    assert backend == frozenset({"be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc"})


def test_slugs_for_team_marketing_is_empty() -> None:
    """Team.MARKETING is legacy — no agent declares it."""
    assert identity.slugs_for_team(identity.Team.MARKETING) == frozenset()


def test_role_for_slug() -> None:
    assert identity.role_for_slug("be-pm") == identity.Role.CELL_PM
    assert identity.role_for_slug("ceo") == identity.Role.CEO


def test_team_for_slug() -> None:
    assert identity.team_for_slug("be-dev-1") == identity.Team.BACKEND
    assert identity.team_for_slug("ceo") == identity.Team.BOARD
    assert identity.team_for_slug("head-marketing") == identity.Team.BOARD
