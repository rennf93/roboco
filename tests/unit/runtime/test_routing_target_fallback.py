"""Routing-target resolution never strands an unassigned pending task.

`_get_routing_target` must always resolve to *some* agent slug — returning
None leaves an ownerless pending task dormant, because no dispatcher re-spawns
an unrouted task. Tasks that can't be placed on a cell (no team, or a non-cell
team like ``fullstack`` / ``system``) and any unrecognized routing fall back to
main-pm, which triages them.
"""

from __future__ import annotations

from typing import Any

from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _resolve(routing: str, team: str | None) -> str | None:
    task: dict[str, Any] = {"id": "t1", "team": team}
    return _orch()._get_routing_target(routing, task)


# ---------------------------------------------------------------------------
# Happy paths still resolve to the right agent
# ---------------------------------------------------------------------------


def test_dev_on_cell_team_selects_cell_agent() -> None:
    assert _resolve("dev", "backend") == "be-dev-1"


def test_board_routes_to_product_owner() -> None:
    assert _resolve("board", None) == "product-owner"


def test_main_pm_routes_to_main_pm() -> None:
    assert _resolve("main_pm", None) == "main-pm"


def test_cell_pm_on_team_routes_to_cell_pm() -> None:
    assert _resolve("cell_pm", "frontend") == "fe-pm"


def test_cell_pm_without_team_falls_back_to_main_pm() -> None:
    assert _resolve("cell_pm", None) == "main-pm"


# ---------------------------------------------------------------------------
# Fallbacks — never None (no dormancy)
# ---------------------------------------------------------------------------


def test_dev_without_team_falls_back_to_main_pm() -> None:
    assert _resolve("dev", None) == "main-pm"


def test_dev_on_non_cell_team_falls_back_to_main_pm() -> None:
    # fullstack / system are valid Team values with no cell agent pool.
    assert _resolve("dev", "fullstack") == "main-pm"
    assert _resolve("dev", "system") == "main-pm"


def test_unknown_routing_falls_back_to_main_pm() -> None:
    assert _resolve("frobnicate", "backend") == "main-pm"


def test_no_routing_ever_returns_none() -> None:
    """Every (routing, team) combination resolves to some agent — never None."""
    routings = ["board", "main_pm", "marketing", "cell_pm", "dev", "bogus"]
    teams: list[str | None] = [None, "backend", "fullstack", "system", "marketing"]
    for routing in routings:
        for team in teams:
            assert _resolve(routing, team) is not None, (routing, team)
