"""Verify that the Role re-export shims point to foundation.identity.Role.

After migration, lifecycle.spec.Role and models.base.AgentRole MUST be the
SAME Python object as foundation.identity.Role. Object identity (`is`) is
the assertion — anything weaker allows silent re-forking.
"""

from __future__ import annotations

from roboco.foundation import identity
from roboco.lifecycle import spec


def test_lifecycle_spec_role_is_foundation_role() -> None:
    assert spec.Role is identity.Role


def test_lifecycle_spec_role_has_system_value() -> None:
    """The new SYSTEM role added to foundation must be visible via the shim."""
    assert "system" in {r.value for r in spec.Role}
