"""Tier 1 — identity self-tests. Fast (no DB, no network)."""

from __future__ import annotations

from enum import IntEnum

from roboco.foundation import identity


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
