"""Unit tests for roboco.api.utils.project.resolve_allowed_agents.

Mocks AgentService (via get_agent_service) so the None-vs-restricted-list
branch and the unresolved-id-drop behavior are exercised without a DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import cast as typing_cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.api.utils.project import resolve_allowed_agents
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from roboco.db.tables import ProjectTable


@pytest.mark.asyncio
async def test_resolve_allowed_agents_returns_none_for_cell_default() -> None:
    project = SimpleNamespace(allowed_agents=None)
    db = MagicMock(spec=AsyncSession)

    with patch("roboco.api.utils.project.get_agent_service") as get_svc:
        result = await resolve_allowed_agents(db, typing_cast("ProjectTable", project))

    assert result is None
    get_svc.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_allowed_agents_resolves_ids_to_summaries() -> None:
    resolvable_id, missing_id = uuid4(), uuid4()
    agent_row = SimpleNamespace(id=resolvable_id, slug="be-dev-1", name="BE Dev 1")
    project = SimpleNamespace(allowed_agents=[resolvable_id, missing_id])
    db = MagicMock(spec=AsyncSession)

    fake_service = AsyncMock()
    fake_service.list_by_ids = AsyncMock(return_value=[agent_row])
    with patch("roboco.api.utils.project.get_agent_service", return_value=fake_service):
        result = await resolve_allowed_agents(db, typing_cast("ProjectTable", project))

    fake_service.list_by_ids.assert_awaited_once_with([resolvable_id, missing_id])
    assert result is not None
    assert len(result) == 1
    assert result[0].id == resolvable_id
    assert result[0].slug == "be-dev-1"
    assert result[0].name == "BE Dev 1"
