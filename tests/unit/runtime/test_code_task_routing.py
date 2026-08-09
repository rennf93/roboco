"""A cell code task must never be classified to board/main_pm by keyword.

A dev/code task whose text contains a board keyword ("launch", "architecture",
"release") must route WITHIN its cell — not to the board (which would then
"review" a dev code task) nor escalated to main_pm (which would own and
deadlock it). The strategic board/cross-cell heuristics apply only to team-less
top-level tasks.

Within a cell, dev-vs-cell_pm is children-aware, not keyword-aware: a leaf
task never routes to cell_pm just because its prose says "review" or
"dependencies" (see the ``has_children`` tests below).
"""

from __future__ import annotations

from typing import Any

from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    return AgentOrchestrator.__new__(AgentOrchestrator)


def _code(
    team: str | None,
    title: str = "x",
    desc: str = "x",
    complexity: str = "medium",
) -> dict[str, Any]:
    return {
        "task_type": "code",
        "team": team,
        "title": title,
        "description": desc,
        "estimated_complexity": complexity,
    }


def test_cell_code_task_with_board_keyword_never_routes_to_board() -> None:
    orch = _orch()
    task = _code(
        "frontend",
        title="Implement the Create & Launch button",
        desc="streaming chat architecture and release flow",
    )
    routing = orch._classify_task_routing(task)
    assert routing != "board"
    assert routing != "main_pm"
    assert routing in ("dev", "cell_pm")


def test_cell_code_high_complexity_routes_to_cell_pm_not_main_pm() -> None:
    orch = _orch()
    assert orch._classify_task_routing(_code("backend", complexity="high")) == "cell_pm"


def test_low_complexity_cell_code_routes_to_dev() -> None:
    orch = _orch()
    assert orch._classify_task_routing(_code("ux_ui", complexity="low")) == "dev"


def test_teamless_code_with_board_keyword_still_routes_to_board() -> None:
    """The strategic heuristics still apply to a team-less top-level task."""
    orch = _orch()
    task = _code(None, title="Quarterly roadmap and launch strategy")
    assert orch._classify_task_routing(task) == "board"


def test_leaf_cell_code_task_with_pm_keywords_routes_to_dev() -> None:
    """The live incident: a childless fix task whose prose says "review" and
    "dependencies" must route to dev, not cell_pm — a cell_pm cannot execute
    code and just plan/delegate/escalate-loops on it (task e0f7651f)."""
    orch = _orch()
    task = _code(
        "backend",
        title="Fix PR #866 revision findings",
        desc="Address reviewer findings, sync branch, update dependencies per review",
    )
    assert orch._classify_task_routing(task, has_children=False) == "dev"


def test_cell_code_task_with_children_routes_to_cell_pm() -> None:
    """A task that already has subtasks is a coordination node, not a leaf —
    it routes to cell_pm regardless of complexity or keywords."""
    orch = _orch()
    task = _code("backend", complexity="low")
    assert orch._classify_task_routing(task, has_children=True) == "cell_pm"


def test_high_complexity_leaf_cell_code_task_routes_to_cell_pm() -> None:
    """A childless task still routes to cell_pm when complexity is high — a
    decomposition candidate, independent of children."""
    orch = _orch()
    task = _code("backend", complexity="high")
    assert orch._classify_task_routing(task, has_children=False) == "cell_pm"
