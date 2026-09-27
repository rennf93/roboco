"""TaskService._MANAGEMENT_ROLES must match the spec's org-wide doctrine.

Found in the 2026-09-27 access-guard regression hunt: the hand-written set
silently lagged lifecycle._ORG_WIDE_ROLES (no ceo, no pr_reviewer). The set
is now DERIVED from the spec, and these tests pin both the derivation and
its claim-time behavior.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from roboco.foundation.identity import Role
from roboco.foundation.policy.lifecycle import _ORG_WIDE_ROLES
from roboco.models import AgentRole
from roboco.services.task import TaskService


def test_management_roles_derive_from_spec_org_wide_set() -> None:
    """Devops is the only subtraction: its cross-cell grant is the
    flag-gated branch inside _validate_claim_team, and an unconditional
    membership would silently un-gate it."""
    assert (
        frozenset(role.value for role in _ORG_WIDE_ROLES - {Role.DEVOPS})
        == TaskService._MANAGEMENT_ROLES
    )
    assert "devops" not in TaskService._MANAGEMENT_ROLES


def test_validate_claim_team_allows_ceo_and_pr_reviewer_cross_team() -> None:
    """The two roles the hand-written set was missing: org-wide actors act
    across cells by design, so the claim-time team check must let them
    through."""
    svc = object.__new__(TaskService)
    task = cast("Any", SimpleNamespace(id=uuid4(), team="backend"))

    for role in (AgentRole.CEO, AgentRole.PR_REVIEWER):
        agent = cast("Any", SimpleNamespace(role=role, team="main_pm"))
        assert svc._validate_claim_team(task, agent) is None


def test_validate_claim_team_still_refuses_cell_agents_cross_team() -> None:
    """The derivation must not become a blanket bypass."""
    svc = object.__new__(TaskService)
    task = cast("Any", SimpleNamespace(id=uuid4(), team="backend"))
    dev = cast("Any", SimpleNamespace(role=AgentRole.DEVELOPER, team="frontend"))

    assert svc._validate_claim_team(task, dev) == "agent not in task's team"
