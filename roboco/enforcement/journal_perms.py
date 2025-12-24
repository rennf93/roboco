"""
Journal Permission Enforcement

Validates who can read whose journal entries.
Permission model mirrors notification/channel access:
- Cell members can read each other's journals (full access including private)
- Cell PMs can read other cells' journals
- Main PM can read all cell journals
- Board can read all journals except CEO/Auditor
- Auditor has silent read access to all journals
- CEO can read all journals
"""

from roboco.agents_config import (
    get_agent_cell,
    get_agent_role,
)
from roboco.exceptions import RobocoError


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


# Protected journals - only readable by CEO/Auditor themselves
PROTECTED_JOURNALS = frozenset(["ceo", "auditor"])


def _is_same_cell(agent1: str, agent2: str) -> bool:
    """Check if two agents are in the same cell."""
    cell1 = get_agent_cell(agent1)
    cell2 = get_agent_cell(agent2)
    return cell1 is not None and cell1 == cell2


# Roles with global read access (can read all non-protected journals)
GLOBAL_READERS = frozenset(
    ["ceo", "auditor", "product_owner", "head_marketing", "main_pm"]
)

# Roles that can read cross-cell PM journals
PM_ROLES = frozenset(["cell_pm", "main_pm"])

# Cell member roles (can only read same-cell journals)
CELL_MEMBER_ROLES = frozenset(["developer", "qa", "documenter"])


def _check_protected_access(
    reader_role: str, owner_id: str, owner_role: str
) -> tuple[bool, str] | None:
    """Check access to protected journals. Returns None if not protected."""
    if owner_id not in PROTECTED_JOURNALS and owner_role not in ("ceo", "auditor"):
        return None  # Not a protected journal
    if reader_role in ("ceo", "auditor"):
        return True, "OK"
    return False, f"Cannot read {owner_role}'s journal - protected"


def _check_cell_pm_access(
    reader_id: str, owner_id: str, owner_role: str
) -> tuple[bool, str]:
    """Check Cell PM's access to another journal."""
    if _is_same_cell(reader_id, owner_id):
        return True, "OK"
    if owner_role in PM_ROLES:
        return True, "OK"
    return False, "Cell PM can only read journals of cell members, other PMs"


def _check_cell_member_access(reader_id: str, owner_id: str) -> tuple[bool, str]:
    """Check cell member's access to another journal."""
    if _is_same_cell(reader_id, owner_id):
        return True, "OK"
    return False, "You can only read journals of your cell members"


def can_read_journal(reader_id: str, owner_id: str) -> tuple[bool, str]:
    """
    Check if reader can access owner's journal.

    Returns:
        Tuple of (can_read, reason)
    """
    if reader_id == owner_id:
        return True, "OK"

    reader_role = get_agent_role(reader_id)
    owner_role = get_agent_role(owner_id)

    # Check protected journals first
    if (
        result := _check_protected_access(reader_role, owner_id, owner_role)
    ) is not None:
        return result

    # Global readers can access all non-protected journals
    if reader_role in GLOBAL_READERS:
        return True, "OK"

    # Cell PM access rules
    if reader_role == "cell_pm":
        return _check_cell_pm_access(reader_id, owner_id, owner_role)

    # Cell member access rules
    if reader_role in CELL_MEMBER_ROLES:
        return _check_cell_member_access(reader_id, owner_id)

    return False, "Unknown role - access denied"


def validate_journal_access(reader_id: str, owner_id: str) -> bool:
    """
    Validate reader can access owner's journal.

    Args:
        reader_id: The agent trying to read (slug)
        owner_id: The journal owner (slug)

    Returns:
        True if allowed

    Raises:
        JournalAccessDeniedError: If access denied
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
    """
    Get information about what journals an agent can read.

    Returns:
        Dict with scope information
    """
    role = get_agent_role(reader_id)
    cell = get_agent_cell(reader_id)

    if role in ("ceo", "auditor"):
        return {"scope": "all", "description": "Can read all journals"}

    if role in ("product_owner", "head_marketing", "main_pm"):
        return {
            "scope": "all_cells",
            "description": "Can read all cell journals",
            "excludes": ["ceo", "auditor"],
        }

    if role == "cell_pm":
        return {
            "scope": "cell_plus_pms",
            "cell": cell,
            "description": f"Can read {cell} cell journals and other PM journals",
        }

    if role in ("developer", "qa", "documenter"):
        return {
            "scope": "cell",
            "cell": cell,
            "description": f"Can read {cell} cell journals only",
        }

    return {"scope": "none", "description": "Unknown role"}
