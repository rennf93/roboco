"""Routing-target resolution never strands an unassigned pending task.

`_get_routing_target` must always resolve to *some* agent slug — returning
None leaves an ownerless pending task dormant, because no dispatcher re-spawns
an unrouted task. Tasks that can't be placed on a cell (no team, or a non-cell
team like ``fullstack`` / ``system``) and any unrecognized routing fall back to
main-pm, which triages them.

EXCEPT for a code-typed task: main-pm is claim-illegal for ``code``
(MAIN_PM_NO_CODE, roboco/services/task.py:9506 — a Main PM coordinates, it
never owns code) — routing such a task to main-pm is a guaranteed permanent
claim-reject loop. The fallback instead redirects to a cell PM: the nearest
ancestor's cell team, or a deterministic default (backend / be-pm) with no
usable parent chain.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


async def _resolve(routing: str, team: str | None, **over: Any) -> str | None:
    task: dict[str, Any] = {"id": "t1", "team": team, **over}
    return await _orch()._get_routing_target(routing, task)


class _CM:
    """Async context manager stand-in for ``session_factory()``."""

    async def __aenter__(self) -> MagicMock:
        return MagicMock()

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _patch_parent_chain(parents: list[MagicMock]) -> Any:
    """Patch the direct-DB parent-chain lookup used by ``_nearest_cell_team``.

    ``parents`` are returned in order from successive ``task_svc.get`` calls,
    mirroring one hop per ancestor.
    """
    svc = MagicMock()
    svc.get = AsyncMock(side_effect=parents)
    factory = MagicMock(return_value=_CM())
    return (
        patch("roboco.db.base.get_session_factory", return_value=factory),
        patch("roboco.services.task.get_task_service", return_value=svc),
    )


# ---------------------------------------------------------------------------
# Happy paths still resolve to the right agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dev_on_cell_team_selects_cell_agent() -> None:
    assert await _resolve("dev", "backend") == "be-dev-1"


@pytest.mark.asyncio
async def test_board_routes_to_product_owner() -> None:
    assert await _resolve("board", None) == "product-owner"


@pytest.mark.asyncio
async def test_main_pm_routes_to_main_pm() -> None:
    assert await _resolve("main_pm", None) == "main-pm"


@pytest.mark.asyncio
async def test_cell_pm_on_team_routes_to_cell_pm() -> None:
    assert await _resolve("cell_pm", "frontend") == "fe-pm"


@pytest.mark.asyncio
async def test_cell_pm_without_team_falls_back_to_main_pm() -> None:
    assert await _resolve("cell_pm", None) == "main-pm"


# ---------------------------------------------------------------------------
# Fallbacks — never None (no dormancy); non-code tasks land on main-pm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dev_without_team_falls_back_to_main_pm() -> None:
    assert await _resolve("dev", None) == "main-pm"


@pytest.mark.asyncio
async def test_dev_on_non_cell_team_falls_back_to_main_pm() -> None:
    # fullstack / system are valid Team values with no cell agent pool.
    assert await _resolve("dev", "fullstack") == "main-pm"
    assert await _resolve("dev", "system") == "main-pm"


@pytest.mark.asyncio
async def test_unknown_routing_falls_back_to_main_pm() -> None:
    assert await _resolve("frobnicate", "backend") == "main-pm"


@pytest.mark.asyncio
async def test_no_routing_ever_returns_none() -> None:
    """Every (routing, team) combination resolves to some agent — never None."""
    routings = ["board", "main_pm", "marketing", "cell_pm", "dev", "bogus"]
    teams: list[str | None] = [None, "backend", "fullstack", "system", "marketing"]
    for routing in routings:
        for team in teams:
            assert await _resolve(routing, team) is not None, (routing, team)


# ---------------------------------------------------------------------------
# Code tasks: main-pm is claim-illegal — redirect to a cell PM instead.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_task_no_team_resolves_parent_cell_pm() -> None:
    """team=None, but the parent task names team=backend -> be-pm, not main-pm."""
    parent = MagicMock(team="backend", parent_task_id=None)
    patches = _patch_parent_chain([parent])
    with patches[0], patches[1]:
        result = await _resolve(
            "dev", None, task_type="code", parent_task_id=str(uuid4())
        )
    assert result == "be-pm"


@pytest.mark.asyncio
async def test_code_task_fullstack_team_no_parent_defaults_to_backend() -> None:
    """team="fullstack" (non-cell), no parent -> deterministic default (be-pm)."""
    result = await _resolve("dev", "fullstack", task_type="code")
    assert result == "be-pm"


@pytest.mark.asyncio
async def test_non_code_task_missing_team_still_routes_to_main_pm() -> None:
    """The redirect is code-only — a planning/admin task may legally sit on main-pm."""
    result = await _resolve("dev", None, task_type="planning")
    assert result == "main-pm"


@pytest.mark.asyncio
async def test_code_task_with_proper_cell_team_unchanged() -> None:
    """A code task that already resolves to a real cell agent is untouched."""
    assert await _resolve("dev", "backend", task_type="code") == "be-dev-1"


@pytest.mark.asyncio
async def test_code_task_walks_multiple_parent_hops() -> None:
    """Grandparent names the cell team; the immediate parent has none."""
    grandparent = MagicMock(team="frontend", parent_task_id=None)
    parent = MagicMock(team=None, parent_task_id=uuid4())
    patches = _patch_parent_chain([parent, grandparent])
    with patches[0], patches[1]:
        result = await _resolve(
            "dev", None, task_type="code", parent_task_id=str(uuid4())
        )
    assert result == "fe-pm"


@pytest.mark.asyncio
async def test_code_task_parent_lookup_failure_defaults_to_backend() -> None:
    """A DB error walking the parent chain must not strand the task either."""
    with patch(
        "roboco.db.base.get_session_factory", side_effect=RuntimeError("db down")
    ):
        result = await _resolve(
            "dev", None, task_type="code", parent_task_id=str(uuid4())
        )
    assert result == "be-pm"
