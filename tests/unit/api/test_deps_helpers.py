"""api.deps coverage — pure permission gate helpers."""

from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

import pytest
from fastapi import HTTPException
from roboco.api.deps import (
    _role_value,
    require_cell_access,
    require_developer_or_above,
    require_pm_or_above,
)
from roboco.models import AgentRole, Team
from roboco.models.permissions import AgentContext


def _ctx(role: AgentRole, team: Team | None = None) -> AgentContext:
    return AgentContext(agent_id=uuid4(), role=role, team=team)


# ---------------------------------------------------------------------------
# _role_value
# ---------------------------------------------------------------------------


def test_role_value_extracts_enum_value() -> None:
    assert _role_value(AgentRole.DEVELOPER) == "developer"


def test_role_value_passes_through_string() -> None:
    assert _role_value("developer") == "developer"


# ---------------------------------------------------------------------------
# require_pm_or_above
# ---------------------------------------------------------------------------


def test_require_pm_or_above_allows_cell_pm() -> None:
    # No raise.
    require_pm_or_above(AgentRole.CELL_PM, "do thing")


def test_require_pm_or_above_allows_main_pm() -> None:
    require_pm_or_above(AgentRole.MAIN_PM, "do thing")


def test_require_pm_or_above_allows_ceo() -> None:
    require_pm_or_above(AgentRole.CEO, "do thing")


def test_require_pm_or_above_denies_developer() -> None:
    with pytest.raises(HTTPException) as exc:
        require_pm_or_above(AgentRole.DEVELOPER, "do thing")
    assert exc.value.status_code == HTTPStatus.FORBIDDEN


def test_require_pm_or_above_denies_qa() -> None:
    with pytest.raises(HTTPException):
        require_pm_or_above(AgentRole.QA, "do thing")


# ---------------------------------------------------------------------------
# require_developer_or_above
# ---------------------------------------------------------------------------


def test_require_developer_or_above_allows_developer() -> None:
    require_developer_or_above(AgentRole.DEVELOPER, "do thing")


def test_require_developer_or_above_allows_ceo() -> None:
    require_developer_or_above(AgentRole.CEO, "do thing")


def test_require_developer_or_above_denies_qa() -> None:
    with pytest.raises(HTTPException):
        require_developer_or_above(AgentRole.QA, "do thing")


# ---------------------------------------------------------------------------
# require_cell_access
# ---------------------------------------------------------------------------


def test_require_cell_access_allows_main_pm() -> None:
    require_cell_access(_ctx(AgentRole.MAIN_PM), Team.BACKEND, "edit")


def test_require_cell_access_allows_ceo_cross_cell() -> None:
    require_cell_access(_ctx(AgentRole.CEO), Team.FRONTEND, "edit")


def test_require_cell_access_allows_cell_member_in_own_team() -> None:
    require_cell_access(
        _ctx(AgentRole.DEVELOPER, team=Team.BACKEND), Team.BACKEND, "edit"
    )


def test_require_cell_access_denies_cross_cell_for_member() -> None:
    with pytest.raises(HTTPException) as exc:
        require_cell_access(
            _ctx(AgentRole.DEVELOPER, team=Team.BACKEND), Team.FRONTEND, "edit"
        )
    assert exc.value.status_code == HTTPStatus.FORBIDDEN
