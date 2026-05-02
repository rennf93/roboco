"""Tests for the orchestrator's task-routing classifier.

Phase 4 follow-up: prevent default code/medium tasks from being routed to
the Cell PM as a re-delegation hop. Single-team code work belongs to a
developer unless the description explicitly names coordination work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def orch(tmp_path: Path) -> AgentOrchestrator:
    """Bare orchestrator instance for classifier-only tests."""
    return AgentOrchestrator(
        blueprints_dir=tmp_path / "blueprints",
        mcp_config_dir=tmp_path / ".mcp",
        project_root=tmp_path,
    )


def _task(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Add login endpoint",
        "description": "Implement POST /login that issues a session token.",
        "task_type": "code",
        "team": "backend",
        "estimated_complexity": "medium",
    }
    base.update(overrides)
    return base


class TestCodeTaskDefaultsToDev:
    def test_medium_complexity_no_pm_keywords_routes_to_dev(
        self, orch: AgentOrchestrator
    ) -> None:
        """Default for a code/medium/single-team task is dev, not cell_pm."""
        task = _task()
        assert orch._classify_task_routing(task) == "dev"

    def test_low_complexity_routes_to_dev(self, orch: AgentOrchestrator) -> None:
        task = _task(estimated_complexity="low")
        assert orch._classify_task_routing(task) == "dev"


class TestComplexityRouting:
    def test_high_complexity_routes_to_main_pm(self, orch: AgentOrchestrator) -> None:
        task = _task(estimated_complexity="high")
        assert orch._classify_task_routing(task) == "main_pm"

    def test_critical_complexity_routes_to_main_pm(
        self, orch: AgentOrchestrator
    ) -> None:
        task = _task(estimated_complexity="critical")
        assert orch._classify_task_routing(task) == "main_pm"


class TestKeywordRouting:
    def test_pm_keyword_in_description_routes_to_cell_pm(
        self, orch: AgentOrchestrator
    ) -> None:
        """Cell PM lights up only when the task names actual coordination."""
        task = _task(description="Coordinate the rollout with the API team.")
        assert orch._classify_task_routing(task) == "cell_pm"

    def test_cross_cell_keyword_routes_to_main_pm(
        self, orch: AgentOrchestrator
    ) -> None:
        task = _task(description="Backend and frontend changes for SSO.")
        assert orch._classify_task_routing(task) == "main_pm"

    def test_board_keyword_routes_to_board(self, orch: AgentOrchestrator) -> None:
        task = _task(description="Update product roadmap for the next quarter.")
        assert orch._classify_task_routing(task) == "board"


class TestTeamlessRouting:
    def test_no_team_routes_to_main_pm(self, orch: AgentOrchestrator) -> None:
        task = _task(team=None)
        assert orch._classify_task_routing(task) == "main_pm"

    def test_team_all_routes_to_main_pm(self, orch: AgentOrchestrator) -> None:
        task = _task(team="all")
        assert orch._classify_task_routing(task) == "main_pm"


class TestNonCodeTaskTypes:
    def test_planning_routes_to_cell_pm_when_team_is_cell(
        self, orch: AgentOrchestrator
    ) -> None:
        task = _task(task_type="planning")
        assert orch._classify_task_routing(task) == "cell_pm"

    def test_planning_routes_to_main_pm_when_no_cell(
        self, orch: AgentOrchestrator
    ) -> None:
        task = _task(task_type="planning", team="board")
        assert orch._classify_task_routing(task) == "main_pm"
