"""propose_postmortem is an Auditor-only manifest grant. Mirrors
test_pest_control_role_grant.py. The Auditor deliberately does NOT also gain
draft_playbook (see test_playbook_verbs.py's "auditor curates but does not
draft" invariant) — a coroner-authored playbook-kind process change is
drafted through a different path (PlaybookService called directly inside
propose_postmortem), never this do-verb."""

from __future__ import annotations

from roboco.services.gateway.role_config import get_role_config


def test_auditor_gets_propose_postmortem() -> None:
    assert "propose_postmortem" in get_role_config("auditor").do_tools


def test_auditor_still_does_not_get_draft_playbook() -> None:
    assert "draft_playbook" not in get_role_config("auditor").do_tools


def test_product_owner_does_not_get_propose_postmortem() -> None:
    assert "propose_postmortem" not in get_role_config("product_owner").do_tools


def test_developer_does_not_get_propose_postmortem() -> None:
    assert "propose_postmortem" not in get_role_config("developer").do_tools
