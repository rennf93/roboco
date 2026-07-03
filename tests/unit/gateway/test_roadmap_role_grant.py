"""propose_roadmap is a Product-Owner-only manifest grant (v1 — HoM stays a
reviewer via the normal board gate; see the roadmap spec's non-goals)."""

from __future__ import annotations

from roboco.services.gateway.role_config import get_role_config


def test_product_owner_gets_propose_roadmap() -> None:
    assert "propose_roadmap" in get_role_config("product_owner").do_tools


def test_head_marketing_does_not_get_propose_roadmap() -> None:
    assert "propose_roadmap" not in get_role_config("head_marketing").do_tools


def test_developer_does_not_get_propose_roadmap() -> None:
    assert "propose_roadmap" not in get_role_config("developer").do_tools
