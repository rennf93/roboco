"""Verify that the Role / Team re-export shims point to foundation.identity.

After migration, ``models.base.AgentRole`` and ``models.base.Team`` MUST be
the SAME Python object as the canonical ``foundation.identity.Role`` /
``foundation.identity.Team``. Object identity (`is`) is the assertion —
anything weaker allows silent re-forking.

The previous ``roboco.lifecycle.spec`` re-export shim was deleted in
Phase 4 Task 8; the two tests that asserted ``spec.Role is identity.Role``
went away with it.
"""

from __future__ import annotations

from roboco.foundation import identity
from roboco.models.base import AgentRole, Team


def test_models_base_agentrole_is_foundation_role() -> None:
    assert AgentRole is identity.Role


def test_models_base_team_is_foundation_team() -> None:
    assert Team is identity.Team
