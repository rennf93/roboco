"""Unit tests for AgentService.list_by_ids.

Mocks the SQLAlchemy AsyncSession.execute() boundary — mirrors
tests/unit/services/test_project.py's pattern for ProjectService.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.services.agent import AgentService

_AGENT_1 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_AGENT_2 = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _result_scalars(rows: list[MagicMock]) -> MagicMock:
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    return result


@pytest.mark.asyncio
async def test_list_by_ids_empty_list_short_circuits_without_query() -> None:
    session = MagicMock()
    session.execute = AsyncMock()
    svc = AgentService(session)

    out = await svc.list_by_ids([])

    assert out == []
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_list_by_ids_returns_matching_rows() -> None:
    row1, row2 = MagicMock(id=_AGENT_1), MagicMock(id=_AGENT_2)
    session = MagicMock()
    session.execute = AsyncMock(return_value=_result_scalars([row1, row2]))
    svc = AgentService(session)

    out = await svc.list_by_ids([_AGENT_1, _AGENT_2, uuid4()])

    assert out == [row1, row2]
    session.execute.assert_called_once()
