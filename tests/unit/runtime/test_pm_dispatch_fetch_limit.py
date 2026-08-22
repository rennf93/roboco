"""_dispatch_pm_work's fetch limit.

``_dispatch_pm_work`` is the one dispatcher whose pending-task fetch is NOT
team-scoped: it triages every fresh pending task org-wide (board/main_pm/
marketing-assigned or not), unlike every other dispatcher, which narrows by
team/source/status and never competes for the same window. ``GET /tasks``'s
own default ``limit`` (100), ordered by priority/sequence/created_at (never
recency), silently truncates once org-wide pending tasks exceed it - a
materialized board-program root (team=main_pm, default priority) can fall
outside the fetched window and simply never be seen: "assigned, team known,
but nothing routes it". These tests pin ``_fetch_tasks`` forwarding an
explicit ``limit`` and ``_dispatch_pm_work`` requesting the API's own
declared max (500, ``GET /tasks``'s ``Query(..., le=500)``) instead of
silently inheriting the 100 default.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.runtime.orchestrator import _PM_DISPATCH_FETCH_LIMIT, AgentOrchestrator


def _orch() -> AgentOrchestrator:
    return AgentOrchestrator.__new__(AgentOrchestrator)


class _RecordingClient:
    """Stands in for httpx.AsyncClient: records every GET call's params and
    returns an empty 200 task list."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get(self, _url: str, params: dict[str, Any]) -> Any:
        self.calls.append(dict(params))
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value=[])
        return resp


@pytest.mark.asyncio
async def test_fetch_tasks_forwards_explicit_limit() -> None:
    orch = _orch()
    client = _RecordingClient()

    await orch._fetch_tasks(cast("Any", client), "pending", limit=500)

    assert client.calls == [{"status": "pending", "limit": 500}]


@pytest.mark.asyncio
async def test_fetch_tasks_omits_limit_by_default() -> None:
    """Every OTHER (team-scoped) dispatcher's fetch keeps the API's own
    100-row default unchanged: no limit param sent unless requested."""
    orch = _orch()
    client = _RecordingClient()

    await orch._fetch_tasks(cast("Any", client), "pending")

    assert client.calls == [{"status": "pending"}]


@pytest.mark.asyncio
async def test_dispatch_pm_work_requests_the_raised_fetch_limit() -> None:
    """_dispatch_pm_work is not team-scoped: it must ask for the API's own
    max (500) instead of the silently-truncating 100 default, or a
    materialized board-program root can fall outside the fetched window and
    never be routed once org-wide pending tasks exceed 100."""
    orch = _orch()
    cast("Any", orch)._fetch_tasks = AsyncMock(return_value=[])
    cast("Any", orch)._is_paused = AsyncMock(return_value=False)

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(orch, client)

    cast("Any", orch)._fetch_tasks.assert_awaited_once_with(
        client, "pending", limit=_PM_DISPATCH_FETCH_LIMIT
    )


def test_pm_dispatch_fetch_limit_matches_api_declared_max() -> None:
    """The chosen limit must equal GET /tasks's own declared ceiling
    (Query(..., le=500)); asking for more would just 422 the request."""
    api_max = 500
    assert api_max == _PM_DISPATCH_FETCH_LIMIT
