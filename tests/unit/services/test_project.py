"""Unit tests for ProjectService.task_counts_for_projects.

Mocks the SQLAlchemy AsyncSession.execute() boundary and verifies the
per-project task-count breakdown (one grouped query over tasks).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from roboco.services.project import ProjectService

_PROJECT_1 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_PROJECT_2 = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _project(pid: UUID | None) -> MagicMock:
    p = MagicMock()
    p.id = pid
    return p


def _result_fetchall(rows: list[MagicMock]) -> MagicMock:
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows)
    return result


def _row(project_id: UUID, done: int, active: int, blocked: int) -> MagicMock:
    row = MagicMock()
    row.project_id = project_id
    row.done = done
    row.active = active
    row.blocked = blocked
    return row


class TestTaskCountsForProjects:
    @pytest.mark.asyncio
    async def test_maps_per_project_counts(self) -> None:
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=_result_fetchall(
                [
                    _row(_PROJECT_1, done=3, active=2, blocked=1),
                    _row(_PROJECT_2, done=5, active=0, blocked=0),
                ]
            )
        )
        svc = ProjectService(session)
        out = await svc.task_counts_for_projects(
            [_project(_PROJECT_1), _project(_PROJECT_2)]
        )
        assert out[_PROJECT_1] == {"done": 3, "active": 2, "blocked": 1}
        assert out[_PROJECT_2] == {"done": 5, "active": 0, "blocked": 0}

    @pytest.mark.asyncio
    async def test_project_with_no_tasks_absent_from_map(self) -> None:
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=_result_fetchall(
                [_row(_PROJECT_1, done=1, active=0, blocked=0)]
            )
        )
        svc = ProjectService(session)
        out = await svc.task_counts_for_projects(
            [_project(_PROJECT_1), _project(_PROJECT_2)]
        )
        # Project 2 has no task row -> absent (route falls back to None).
        assert _PROJECT_1 in out
        assert _PROJECT_2 not in out

    @pytest.mark.asyncio
    async def test_no_projects_no_query(self) -> None:
        session = MagicMock()
        session.execute = AsyncMock()
        svc = ProjectService(session)
        out = await svc.task_counts_for_projects([])
        assert out == {}
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_projects_without_id(self) -> None:
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=_result_fetchall(
                [_row(_PROJECT_1, done=2, active=1, blocked=0)]
            )
        )
        svc = ProjectService(session)
        out = await svc.task_counts_for_projects([_project(_PROJECT_1), _project(None)])
        assert out[_PROJECT_1] == {"done": 2, "active": 1, "blocked": 0}
