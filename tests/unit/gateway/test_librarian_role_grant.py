"""propose_playbook_drafts is an Auditor-only manifest grant. Mirrors
test_sentinel_role_grant.py / test_coroner_role_grant.py. The Auditor
deliberately does NOT also gain draft_playbook (see test_playbook_verbs.py's
"auditor curates but does not draft" invariant) — a Librarian-authored
playbook draft is created through a different path (PlaybookService called
directly inside propose_playbook_drafts), never this do-verb — the exact
same precedent test_coroner_role_grant.py already locked for Coroner."""

from __future__ import annotations

from roboco.services.gateway.role_config import get_role_config


def test_auditor_gets_propose_playbook_drafts() -> None:
    assert "propose_playbook_drafts" in get_role_config("auditor").do_tools


def test_auditor_still_does_not_get_draft_playbook() -> None:
    assert "draft_playbook" not in get_role_config("auditor").do_tools


def test_product_owner_does_not_get_propose_playbook_drafts() -> None:
    assert "propose_playbook_drafts" not in get_role_config("product_owner").do_tools


def test_head_marketing_does_not_get_propose_playbook_drafts() -> None:
    assert "propose_playbook_drafts" not in get_role_config("head_marketing").do_tools


def test_developer_does_not_get_propose_playbook_drafts() -> None:
    assert "propose_playbook_drafts" not in get_role_config("developer").do_tools
