"""Role-asserting dependencies and shared helpers for v1 flow routers.

Every router gets one of these as a dependency so the role check happens
before the choreographer body even runs. Defense in depth — the
choreographer also re-checks role internally for verbs that branch on it.

The actual helper implementations live in
``roboco.api.utils.v1_role_dep`` (routes stay thin handlers per the
architectural map); this module re-imports them and builds the
module-level per-role guard instances every flow router depends on.
"""

from __future__ import annotations

from roboco.api.utils.v1_role_dep import (
    _log_rejection,
    _require_authenticated_agent,
    _require_roles,
    envelope_to_response,
)
from roboco.foundation.identity import Role

__all__ = [
    "_log_rejection",
    "_require_authenticated_agent",
    "_require_roles",
    "envelope_to_response",
    "require_any_authenticated_agent",
    "require_auditor",
    "require_board",
    "require_cell_pm",
    "require_dev",
    "require_doc",
    "require_main_pm",
    "require_pr_reviewer",
    "require_qa",
]

# Role-typed single-role guards — renaming a role edits foundation.identity only.
# `require_board` is the only multi-role guard (Product Owner + Head of Marketing
# share the public-facing board endpoints; the auditor has its own guard).
require_dev = _require_roles(frozenset({Role.DEVELOPER}))
require_qa = _require_roles(frozenset({Role.QA}))
require_doc = _require_roles(frozenset({Role.DOCUMENTER}))
require_cell_pm = _require_roles(frozenset({Role.CELL_PM}))
require_main_pm = _require_roles(frozenset({Role.MAIN_PM}))
require_board = _require_roles(frozenset({Role.PRODUCT_OWNER, Role.HEAD_MARKETING}))
require_auditor = _require_roles(frozenset({Role.AUDITOR}))
require_pr_reviewer = _require_roles(frozenset({Role.PR_REVIEWER}))

# The do router serves all roles, so this is token-only (no role assertion).
require_any_authenticated_agent = _require_authenticated_agent()
