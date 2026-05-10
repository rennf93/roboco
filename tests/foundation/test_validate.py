"""Tier 1 — validator self-tests.

Each test temporarily corrupts a foundation table, runs the validator,
asserts it raises a clear error, then restores the table.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from roboco.foundation import _validate, identity

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def _patched_agents(patch: dict[str, identity.AgentRow]) -> Iterator[None]:
    original = dict(identity.AGENTS)
    identity.AGENTS.update(patch)
    try:
        yield
    finally:
        identity.AGENTS.clear()
        identity.AGENTS.update(original)


def test_validators_pass_on_pristine_state() -> None:
    """All validators pass against the real AGENTS table."""
    _validate.run_all()  # no exception


def test_duplicate_uuid_fails() -> None:
    """Two slugs sharing a UUID is rejected."""
    duplicate = identity.AgentRow(
        slug="rogue-1",
        role=identity.Role.DEVELOPER,
        team=identity.Team.BACKEND,
        uuid=identity.AGENTS["be-dev-1"].uuid,  # same UUID as be-dev-1
    )
    with _patched_agents({"rogue-1": duplicate}):
        with pytest.raises(_validate.IdentityValidationError) as exc_info:
            _validate.run_all()
        assert "duplicate UUID" in str(exc_info.value)


def test_role_without_agent_fails_except_system() -> None:
    """Every Role except SYSTEM must have at least one agent."""
    # All real roles have agents in pristine state, so this passes.
    _validate.run_all()
    # Removing all developers should fail:
    no_devs = {
        slug: row
        for slug, row in identity.AGENTS.items()
        if row.role != identity.Role.DEVELOPER
    }
    original = dict(identity.AGENTS)
    identity.AGENTS.clear()
    identity.AGENTS.update(no_devs)
    try:
        with pytest.raises(_validate.IdentityValidationError) as exc_info:
            _validate.run_all()
        assert "developer" in str(exc_info.value).lower()
    finally:
        identity.AGENTS.clear()
        identity.AGENTS.update(original)


def test_role_level_covers_all_roles() -> None:
    """ROLE_LEVEL must cover every Role."""
    _validate.run_all()
    # Removing one role's level entry would fail:
    original_entry = identity.ROLE_LEVEL.pop(identity.Role.DEVELOPER)
    try:
        with pytest.raises(_validate.IdentityValidationError):
            _validate.run_all()
    finally:
        identity.ROLE_LEVEL[identity.Role.DEVELOPER] = original_entry


def test_pm_roles_consistent_with_agents() -> None:
    """PM_ROLES are populated by at least one agent each (CELL_PM and MAIN_PM)."""
    _validate.run_all()
