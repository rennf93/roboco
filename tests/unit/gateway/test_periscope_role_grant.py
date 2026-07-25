"""propose_market_brief is a Head-of-Marketing-only manifest grant, mirroring
propose_feature_spotlight."""

from __future__ import annotations

from roboco.services.gateway.role_config import get_role_config


def test_head_marketing_gets_propose_market_brief() -> None:
    assert "propose_market_brief" in get_role_config("head_marketing").do_tools


def test_product_owner_does_not_get_propose_market_brief() -> None:
    assert "propose_market_brief" not in get_role_config("product_owner").do_tools


def test_developer_does_not_get_propose_market_brief() -> None:
    assert "propose_market_brief" not in get_role_config("developer").do_tools
