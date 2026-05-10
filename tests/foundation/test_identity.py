"""Tier 1 — identity self-tests. Fast (no DB, no network)."""

from __future__ import annotations

from enum import IntEnum

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
        "qa",
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
