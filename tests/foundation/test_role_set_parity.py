"""Role-sets are now canonical in foundation; consumers must derive."""

from __future__ import annotations

from roboco.agents_config import _BOARD_ROLES, TASK_CREATOR_ROLES
from roboco.foundation import identity
from roboco.services import permissions as perms


def test_agents_config_task_creator_roles_is_5_roles() -> None:
    """The 5-role 'PMs+board+CEO' set in agents_config is renamed to TASK_CREATOR_ROLES.

    Pre-migration: agents_config.py defined PM_ROLES = {cell_pm, main_pm,
    product_owner, head_marketing, ceo} — but this is the "roles that can
    create tasks", NOT the PM hierarchy. Renamed to TASK_CREATOR_ROLES.
    """
    expected = frozenset(
        {
            identity.Role.CELL_PM,
            identity.Role.MAIN_PM,
            identity.Role.PRODUCT_OWNER,
            identity.Role.HEAD_MARKETING,
            identity.Role.CEO,
        }
    )
    assert expected == TASK_CREATOR_ROLES


def test_services_permissions_pm_roles_is_foundation() -> None:
    """services/permissions.PM_ROLES (2-role variant) is now foundation.PM_ROLES.

    Object identity (`is`) — not just equality.
    """
    assert perms.PM_ROLES is identity.PM_ROLES


def test_agents_config_board_roles_aliased_to_foundation() -> None:
    """agents_config._BOARD_ROLES is foundation.BOARD_ROLES (3 roles, no main_pm)."""
    assert _BOARD_ROLES == identity.BOARD_ROLES
    # Specifically: no MAIN_PM (main_pm is below board, not part of it).
    assert identity.Role.MAIN_PM not in _BOARD_ROLES
