"""propose_quality_report is an Auditor-only manifest grant, mirroring
propose_market_brief."""

from __future__ import annotations

from roboco.services.gateway.role_config import get_role_config


def test_auditor_gets_propose_quality_report() -> None:
    assert "propose_quality_report" in get_role_config("auditor").do_tools


def test_product_owner_does_not_get_propose_quality_report() -> None:
    assert "propose_quality_report" not in get_role_config("product_owner").do_tools


def test_head_marketing_does_not_get_propose_quality_report() -> None:
    assert "propose_quality_report" not in get_role_config("head_marketing").do_tools


def test_developer_does_not_get_propose_quality_report() -> None:
    assert "propose_quality_report" not in get_role_config("developer").do_tools
