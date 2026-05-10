"""Foundation cross-table validators. Run at import time.

If any validator raises, the orchestrator container won't start —
which is the correct behavior for a misconfigured foundation.

Roles excluded from "must have at least one agent": Role.SYSTEM
(sentinel-only). All other validators apply to every role.
"""

from __future__ import annotations

from collections import Counter

from roboco.foundation import identity

_SENTINEL_ROLES: frozenset[identity.Role] = frozenset({identity.Role.SYSTEM})


class IdentityValidationError(RuntimeError):
    """Raised at import time when foundation/identity tables are inconsistent."""


def _check_unique_uuids() -> None:
    counts = Counter(row.uuid for row in identity.AGENTS.values())
    dupes = {uuid for uuid, n in counts.items() if n > 1}
    if dupes:
        raise IdentityValidationError(
            f"duplicate UUID in AGENTS: {sorted(map(str, dupes))}"
        )


def _check_unique_slugs() -> None:
    """Dict guarantees this; validator catches accidental mutation."""
    if len(identity.AGENTS) != len(set(identity.AGENTS)):
        raise IdentityValidationError(
            "AGENTS has duplicate slugs (impossible via dict, but checked anyway)"
        )


def _check_every_real_role_has_agent() -> None:
    """Every Role.X (except SYSTEM) must have at least one agent."""
    roles_with_agents = {row.role for row in identity.AGENTS.values()}
    missing = set(identity.Role) - roles_with_agents - _SENTINEL_ROLES
    if missing:
        names = ", ".join(sorted(r.value for r in missing))
        raise IdentityValidationError(f"roles with no agent in AGENTS: {names}")


def _check_role_level_covers_all_roles() -> None:
    missing = set(identity.Role) - set(identity.ROLE_LEVEL)
    if missing:
        names = ", ".join(sorted(r.value for r in missing))
        raise IdentityValidationError(f"ROLE_LEVEL missing entries for: {names}")


def _check_pm_roles_have_agents() -> None:
    for role in identity.PM_ROLES:
        if not identity.slugs_for_role(role):
            raise IdentityValidationError(
                f"Role.{role.name} is in PM_ROLES but no agent has it"
            )


def _check_board_roles_have_agents() -> None:
    for role in identity.BOARD_ROLES:
        if not identity.slugs_for_role(role):
            raise IdentityValidationError(
                f"Role.{role.name} is in BOARD_ROLES but no agent has it"
            )


_VALIDATORS = (
    _check_unique_uuids,
    _check_unique_slugs,
    _check_every_real_role_has_agent,
    _check_role_level_covers_all_roles,
    _check_pm_roles_have_agents,
    _check_board_roles_have_agents,
)


def run_all() -> None:
    """Run every validator. First failure raises IdentityValidationError."""
    for validator in _VALIDATORS:
        validator()
