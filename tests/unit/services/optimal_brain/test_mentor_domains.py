"""MentorService domain index selection — the CEO's own vault notes join the
general/company default bucket (not the coding/security/workflow domains,
which stay code/process-focused)."""

from __future__ import annotations

from roboco.models.optimal import IndexType
from roboco.services.optimal_brain.mentor import MentorService


def test_default_domain_includes_vault_notes() -> None:
    assert IndexType.VAULT_NOTES in MentorService()._get_indexes_for_domain(None)


def test_coding_domain_excludes_vault_notes() -> None:
    assert IndexType.VAULT_NOTES not in MentorService()._get_indexes_for_domain(
        "coding"
    )


def test_security_domain_excludes_vault_notes() -> None:
    assert IndexType.VAULT_NOTES not in MentorService()._get_indexes_for_domain(
        "security"
    )


def test_workflow_domain_excludes_vault_notes() -> None:
    assert IndexType.VAULT_NOTES not in MentorService()._get_indexes_for_domain(
        "workflow"
    )
