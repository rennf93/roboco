"""Journal Permission Enforcement.

Read-tier rules are canonical in :mod:`roboco.foundation.policy.journaling`.
This module translates the foundation tiers (`ROLE_READ_TIERS`,
`PROTECTED_JOURNALS`) onto the project's existing public surface
(`can_read_journal`, `validate_journal_access`, `get_readable_journals`,
`JournalAccessDeniedError`).

Permission model (now derived from foundation tiers):
- Self-read is always allowed.
- Protected journals (ceo, auditor) can only be read by `ReadTier.ALL`
  roles (CEO, Auditor) — even other "global" readers are excluded.
- `ReadTier.ALL_CELLS` (Main PM, Product Owner, Head of Marketing) reads
  every non-protected journal.
- `ReadTier.CELL_AND_PMS` (Cell PM) reads same-cell members and other PMs.
- `ReadTier.CELL` (Developer, QA, Documenter) reads same-cell members only.
- `ReadTier.OWN` (System sentinel, unknown roles) reads nothing but their own.
"""

from __future__ import annotations

from roboco.agents_config import (
    get_agent_cell,
    get_agent_role,
)
from roboco.exceptions import RobocoError
from roboco.foundation.identity import Role
from roboco.foundation.policy.journaling import (
    PROTECTED_JOURNALS,
    ROLE_READ_TIERS,
    ReadTier,
)

__all__ = [
    "PROTECTED_JOURNALS",
    "JournalAccessDeniedError",
    "can_read_journal",
    "get_readable_journals",
    "validate_journal_access",
]


class JournalAccessDeniedError(RobocoError):
    """Raised when an agent doesn't have permission to read a journal."""

    def __init__(
        self,
        reader_id: str,
        owner_id: str,
        message: str | None = None,
    ):
        self.reader_id = reader_id
        self.owner_id = owner_id
        super().__init__(
            code="JOURNAL_ACCESS_DENIED",
            message=message or f"Agent {reader_id} cannot read journal of {owner_id}",
            details={
                "reader_id": reader_id,
                "owner_id": owner_id,
            },
        )


def _resolve_role(role_str: str) -> Role | None:
    """Map a string role (as returned by `get_agent_role`) to the Role enum.

    Returns None for unknown / sentinel roles so callers can deny by default.
    """
    try:
        return Role(role_str)
    except ValueError:
        return None


def _tier_for(role_str: str) -> ReadTier:
    """Read tier for a given role string. Unknown roles get OWN (deny)."""
    role = _resolve_role(role_str)
    if role is None:
        return ReadTier.OWN
    return ROLE_READ_TIERS.get(role, ReadTier.OWN)


def _is_same_cell(reader_id: str, owner_id: str) -> bool:
    cell1 = get_agent_cell(reader_id)
    cell2 = get_agent_cell(owner_id)
    return cell1 is not None and cell1 == cell2


def _is_pm_role(role_str: str) -> bool:
    role = _resolve_role(role_str)
    return role in (Role.CELL_PM, Role.MAIN_PM)


def _decide_protected(tier: ReadTier, owner_role_str: str) -> tuple[bool, str]:
    """Access decision when the target journal is protected."""
    if tier == ReadTier.ALL:
        return True, "OK"
    return False, f"Cannot read {owner_role_str}'s journal - protected"


def _decide_by_tier(
    tier: ReadTier,
    *,
    same_cell: bool,
    owner_is_pm: bool,
) -> tuple[bool, str]:
    """Access decision for non-protected journals, dispatched by tier."""
    if tier in (ReadTier.ALL, ReadTier.ALL_CELLS):
        return True, "OK"
    if tier == ReadTier.CELL_AND_PMS:
        if same_cell or owner_is_pm:
            return True, "OK"
        return False, "Cell PM can only read journals of cell members, other PMs"
    if tier == ReadTier.CELL:
        if same_cell:
            return True, "OK"
        return False, "You can only read journals of your cell members"
    return False, "Unknown role - access denied"


def can_read_journal(reader_id: str, owner_id: str) -> tuple[bool, str]:
    """Check if `reader_id` can access `owner_id`'s journal.

    Returns a `(can_read, reason)` tuple. The reason is a human-readable
    string suitable for surfacing in error envelopes.
    """
    if reader_id == owner_id:
        return True, "OK"

    reader_role_str = get_agent_role(reader_id)
    owner_role_str = get_agent_role(owner_id)
    tier = _tier_for(reader_role_str)

    if owner_id in PROTECTED_JOURNALS or owner_role_str in ("ceo", "auditor"):
        return _decide_protected(tier, owner_role_str)
    return _decide_by_tier(
        tier,
        same_cell=_is_same_cell(reader_id, owner_id),
        owner_is_pm=_is_pm_role(owner_role_str),
    )


def validate_journal_access(reader_id: str, owner_id: str) -> bool:
    """Validate reader can access owner's journal.

    Args:
        reader_id: The agent trying to read (slug)
        owner_id: The journal owner (slug)

    Returns:
        True if allowed.

    Raises:
        JournalAccessDeniedError: If access denied.
    """
    can_read, reason = can_read_journal(reader_id, owner_id)
    if not can_read:
        raise JournalAccessDeniedError(
            reader_id=reader_id,
            owner_id=owner_id,
            message=reason,
        )
    return True


def get_readable_journals(reader_id: str) -> dict:
    """Describe what journals an agent can read.

    The returned dict's `scope` field is one of `all`, `all_cells`,
    `cell_plus_pms`, `cell`, or `none` — kept for public API parity
    with the pre-foundation surface.
    """
    role_str = get_agent_role(reader_id)
    cell = get_agent_cell(reader_id)
    tier = _tier_for(role_str)

    if tier == ReadTier.ALL:
        return {"scope": "all", "description": "Can read all journals"}
    if tier == ReadTier.ALL_CELLS:
        return {
            "scope": "all_cells",
            "description": "Can read all cell journals",
            "excludes": list(PROTECTED_JOURNALS),
        }
    if tier == ReadTier.CELL_AND_PMS:
        return {
            "scope": "cell_plus_pms",
            "cell": cell,
            "description": f"Can read {cell} cell journals and other PM journals",
        }
    if tier == ReadTier.CELL:
        return {
            "scope": "cell",
            "cell": cell,
            "description": f"Can read {cell} cell journals only",
        }
    return {"scope": "none", "description": "Unknown role"}
