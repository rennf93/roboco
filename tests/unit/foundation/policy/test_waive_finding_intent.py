"""IntentSpec for the auditor ``waive_finding`` verb.

``mark_waived`` sat unwired on the repository since the findings ledger
landed (PR #486) — a deliberate follow-up. The auditor is the role that
can close a finding without a dev fix, but only for non-blocking severity.
This pins the spec: auditor-only, severity-scoped at the verb body (the
IntentSpec carries no task precondition — ``composes=()`` like ``triage``).
"""

from __future__ import annotations

from roboco.foundation.identity import Role
from roboco.foundation.policy.lifecycle import intents_for_role
from roboco.services.gateway.role_config import _AUDITOR_FLOW


def test_waive_finding_is_an_auditor_flow_verb() -> None:
    assert "waive_finding" in intents_for_role(Role.AUDITOR)
    assert "waive_finding" in _AUDITOR_FLOW


def test_waive_finding_is_auditor_only() -> None:
    for role in (
        Role.DEVELOPER,
        Role.QA,
        Role.DOCUMENTER,
        Role.CELL_PM,
        Role.MAIN_PM,
        Role.PR_REVIEWER,
        Role.PRODUCT_OWNER,
        Role.HEAD_MARKETING,
    ):
        assert "waive_finding" not in intents_for_role(role), (
            f"{role} must not get waive_finding"
        )
