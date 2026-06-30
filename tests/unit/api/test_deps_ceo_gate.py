"""Shared CEO role gate (#25).

Two ``_require_ceo`` implementations had drifted apart: the orchestrator
router-level one (header + HMAC token) compared ``Role.CEO.value``; the release
handler-level one compared ``AgentRole.CEO``. Both enforced CEO correctly, but
the duplicated role check could drift. The single ``require_ceo_role`` helper in
deps is now the one source of truth both call — same 403, same role set.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from roboco.api.deps import require_ceo_role
from roboco.foundation.identity import Role
from roboco.models import AgentRole

_FORBIDDEN = 403  # HTTP 403 Forbidden — the gate's reject status


def test_require_ceo_role_accepts_ceo_in_any_form() -> None:
    # The helper must accept the enum (AgentRole / Role) and the lowercase
    # header string the orchestrator router passes.
    require_ceo_role(AgentRole.CEO)
    require_ceo_role(Role.CEO)
    require_ceo_role("ceo")


@pytest.mark.parametrize(
    "role",
    [
        AgentRole.DEVELOPER,
        AgentRole.CELL_PM,
        AgentRole.MAIN_PM,
        AgentRole.AUDITOR,
        AgentRole.QA,
        "developer",
        "main_pm",
    ],
)
def test_require_ceo_role_rejects_non_ceo(role: object) -> None:
    with pytest.raises(HTTPException) as exc:
        require_ceo_role(role)
    assert exc.value.status_code == _FORBIDDEN
