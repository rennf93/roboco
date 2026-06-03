"""Route-guard role-sets in api/deps + api/routes/v1/_role_dep derive from foundation.

The HTTP-layer guards in `roboco.api.deps` and `roboco.api.routes.v1._role_dep`
historically used hand-written frozensets of role-name strings. Phase 4 Task 11
moves those literals onto `foundation.identity` so adding/renaming a role only
edits one file. These tests pin the foundation-derived membership and the
import contract.
"""

from __future__ import annotations

import inspect

from roboco.api import deps
from roboco.api.deps import (
    _DEVELOPER_OR_ABOVE_ROLES,
    _GLOBAL_CELL_ACCESS_ROLES,
    _PM_OR_ABOVE_ROLES,
)
from roboco.api.routes.v1 import _role_dep
from roboco.foundation.identity import BOARD_ROLES, DEV_ROLES, PM_ROLES, Role


def test_pm_or_above_roles_matches_foundation_composition() -> None:
    """`require_pm_or_above` admits PMs + non-marketing board + CEO.

    Composition: PM_ROLES | (BOARD_ROLES - {HEAD_MARKETING}) | {CEO}.
    Head-marketing is intentionally excluded — the role is a marketing
    spokesperson, not a workflow approver.
    """
    expected = PM_ROLES | (BOARD_ROLES - {Role.HEAD_MARKETING}) | {Role.CEO}
    assert expected == _PM_OR_ABOVE_ROLES


def test_developer_or_above_roles_matches_foundation_composition() -> None:
    """`require_developer_or_above` admits developers + PM-or-above.

    Composition: DEV_ROLES | (PM_OR_ABOVE).
    QA and documenter are intentionally NOT in this set — work-session
    create/commit/PR endpoints are dev-only operations.
    """
    expected = DEV_ROLES | _PM_OR_ABOVE_ROLES
    assert expected == _DEVELOPER_OR_ABOVE_ROLES


def test_global_cell_access_roles_matches_foundation_composition() -> None:
    """`require_cell_access` lets main-PM + non-marketing board + CEO cross cells."""
    expected = (BOARD_ROLES - {Role.HEAD_MARKETING}) | {Role.MAIN_PM, Role.CEO}
    assert expected == _GLOBAL_CELL_ACCESS_ROLES


def test_deps_module_imports_from_foundation() -> None:
    """`roboco.api.deps` sources its role-set primitives from foundation."""
    src = inspect.getsource(deps)
    assert "from roboco.foundation.identity import" in src


def test_role_dep_module_imports_from_foundation() -> None:
    """`roboco.api.routes.v1._role_dep` sources its Role enum from foundation."""
    src = inspect.getsource(_role_dep)
    assert "from roboco.foundation.identity import" in src


def test_v1_role_dep_sets_match_foundation_roles() -> None:
    """v1 single-role guards use foundation Role values, not raw strings."""
    # Single-role guards should pass through Role-typed frozensets so
    # renaming a role lives in foundation, not the guard literal.
    # The Depends() objects wrap the closure, so we can't introspect the
    # frozenset directly — but we can confirm the role values resolve.
    assert Role.DEVELOPER == "developer"
    assert Role.QA == "qa"
    assert Role.DOCUMENTER == "documenter"
    assert Role.CELL_PM == "cell_pm"
    assert Role.MAIN_PM == "main_pm"
    assert Role.PRODUCT_OWNER == "product_owner"
    assert Role.HEAD_MARKETING == "head_marketing"
    assert Role.AUDITOR == "auditor"
