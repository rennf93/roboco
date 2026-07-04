"""propose_feature_spotlight is a Head-of-Marketing-only manifest grant
(mirrors propose_roadmap's PO-only symmetry, reversed)."""

from __future__ import annotations

from roboco.services.gateway.role_config import get_role_config


def test_head_marketing_gets_propose_feature_spotlight() -> None:
    assert "propose_feature_spotlight" in get_role_config("head_marketing").do_tools


def test_product_owner_does_not_get_propose_feature_spotlight() -> None:
    assert "propose_feature_spotlight" not in get_role_config("product_owner").do_tools


def test_developer_does_not_get_propose_feature_spotlight() -> None:
    assert "propose_feature_spotlight" not in get_role_config("developer").do_tools
