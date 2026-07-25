"""propose_rebalance is a Product-Owner-only manifest grant, mirroring
propose_bug_hunt/propose_roadmap."""

from __future__ import annotations

from roboco.services.gateway.role_config import get_role_config


def test_product_owner_gets_propose_rebalance() -> None:
    assert "propose_rebalance" in get_role_config("product_owner").do_tools


def test_head_marketing_does_not_get_propose_rebalance() -> None:
    assert "propose_rebalance" not in get_role_config("head_marketing").do_tools


def test_developer_does_not_get_propose_rebalance() -> None:
    assert "propose_rebalance" not in get_role_config("developer").do_tools
