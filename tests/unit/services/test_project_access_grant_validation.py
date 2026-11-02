"""add_allowed_agent grant validation (2026-09-27 access-guard hunt, F2).

The allowed-agents list NARROWS within the assigned cell (the cell check
precedes the list check in check_agent_access), so granting a cross-cell or
nonexistent agent flips the project from whole-cell access to list-only
while the added agent gains nothing — one bad call locks out the entire
cell. The services-layer write path now rejects both.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.exceptions import ValidationError
from roboco.services.base import NotFoundError
from roboco.services.project import ProjectService

# A fake row shaped for the real ProjectService.get path (execute ->
# scalar_one_or_none -> _register_forge, which reads git_url/git_provider).
_GIT_URL = "https://github.com/acme/repo.git"


def _project_row(project_id: Any) -> MagicMock:
    return MagicMock(
        id=project_id,
        assigned_cell="backend",
        allowed_agents=None,
        git_url=_GIT_URL,
        git_provider="github",
    )


def _service(
    project: MagicMock | None, agent: MagicMock | None
) -> tuple[ProjectService, AsyncMock]:
    session = AsyncMock()
    session.execute.return_value = MagicMock(
        scalar_one_or_none=MagicMock(return_value=project)
    )
    session.get.return_value = agent
    return ProjectService(session), session


@pytest.mark.asyncio
async def test_add_allowed_agent_rejects_cross_cell_agent() -> None:
    project_id, agent_id = uuid4(), uuid4()
    project, agent = _project_row(project_id), MagicMock(id=agent_id, team="frontend")
    svc, session = _service(project, agent)

    with pytest.raises(ValidationError) as exc:
        await svc.add_allowed_agent(project_id, agent_id)
    assert "narrows within" in str(exc.value)
    # The refusal must leave the grant list untouched — the whole failure
    # mode is the whole-cell lockout a flip to list-only causes.
    assert project.allowed_agents is None
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_allowed_agent_rejects_unknown_agent() -> None:
    project_id, agent_id = uuid4(), uuid4()
    project = _project_row(project_id)
    svc, session = _service(project, None)

    with pytest.raises(NotFoundError):
        await svc.add_allowed_agent(project_id, agent_id)
    assert project.allowed_agents is None
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_allowed_agent_grants_same_cell_agent() -> None:
    project_id, agent_id = uuid4(), uuid4()
    project = _project_row(project_id)
    agent = MagicMock(id=agent_id, team="backend")
    svc, session = _service(project, agent)

    out = await svc.add_allowed_agent(project_id, agent_id)
    assert out is project
    assert project.allowed_agents == [agent_id]
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_allowed_agent_survives_teamless_agent() -> None:
    """team is nullable — a teamless agent belongs to no cell and is not
    grantable (None != assigned_cell)."""
    project_id, agent_id = uuid4(), uuid4()
    project = _project_row(project_id)
    agent = MagicMock(id=agent_id, team=None)
    svc, _ = _service(project, agent)

    with pytest.raises(ValidationError):
        await svc.add_allowed_agent(project_id, agent_id)
