"""propose_friction_fixes is a Product-Owner-only manifest grant, mirroring
propose_gap_fill."""

from __future__ import annotations

from roboco.services.gateway.role_config import get_role_config


def test_product_owner_gets_propose_friction_fixes() -> None:
    assert "propose_friction_fixes" in get_role_config("product_owner").do_tools


def test_head_marketing_does_not_get_propose_friction_fixes() -> None:
    assert "propose_friction_fixes" not in get_role_config("head_marketing").do_tools


def test_developer_does_not_get_propose_friction_fixes() -> None:
    assert "propose_friction_fixes" not in get_role_config("developer").do_tools
