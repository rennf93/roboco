"""
PM Agent Factories

Factory functions for creating PM agents (Cell PMs and Main PM).
"""

from roboco.agents.factories._base import load_blueprint_prompt, make_slug
from roboco.agents.pm import CellPMAgent, MainPMAgent
from roboco.models import AgentRole, Team
from roboco.models.agents import AgentConfig

# Blueprint paths for cell PMs
_CELL_PM_BLUEPRINTS = {
    Team.BACKEND: "agents/blueprints/backend/be-pm.md",
    Team.FRONTEND: "agents/blueprints/frontend/fe-pm.md",
    Team.UX_UI: "agents/blueprints/ux_ui/ux-pm.md",
}

# Default prompts for cell PMs
_CELL_PM_PROMPTS = {
    Team.BACKEND: "You are the Backend Cell PM.",
    Team.FRONTEND: "You are the Frontend Cell PM.",
    Team.UX_UI: "You are the UX/UI Cell PM.",
}


def _create_cell_pm(
    name: str,
    team: Team,
    system_prompt: str | None = None,
) -> CellPMAgent:
    """
    Internal factory for creating a cell PM agent.

    Args:
        name: Agent display name
        team: Team assignment
        system_prompt: Optional custom system prompt

    Returns:
        Configured CellPMAgent instance
    """
    if system_prompt is None:
        system_prompt = load_blueprint_prompt(
            _CELL_PM_BLUEPRINTS[team],
            _CELL_PM_PROMPTS[team],
        )

    config = AgentConfig(
        name=name,
        slug=make_slug(name),
        role=AgentRole.CELL_PM,
        team=team,
        system_prompt=system_prompt,
        capabilities=["task_management", "notifications"],
        can_notify=True,
    )

    return CellPMAgent(config)


def create_backend_pm(
    name: str = "BE-PM",
    system_prompt: str | None = None,
) -> CellPMAgent:
    """Factory function to create a backend PM agent."""
    return _create_cell_pm(name, Team.BACKEND, system_prompt)


def create_frontend_pm(
    name: str = "FE-PM",
    system_prompt: str | None = None,
) -> CellPMAgent:
    """Factory function to create a frontend PM agent."""
    return _create_cell_pm(name, Team.FRONTEND, system_prompt)


def create_ux_pm(
    name: str = "UX-PM",
    system_prompt: str | None = None,
) -> CellPMAgent:
    """Factory function to create a UX/UI PM agent."""
    return _create_cell_pm(name, Team.UX_UI, system_prompt)


def create_main_pm(
    name: str = "Main PM",
    system_prompt: str | None = None,
) -> MainPMAgent:
    """Factory function to create the Main PM agent."""
    if system_prompt is None:
        system_prompt = load_blueprint_prompt(
            "agents/blueprints/board/main-pm.md",
            "You are the Main PM coordinating all cells.",
        )

    config = AgentConfig(
        name=name,
        slug="main-pm",
        role=AgentRole.MAIN_PM,
        team=Team.BOARD,
        system_prompt=system_prompt,
        capabilities=["task_management", "notifications", "cross_cell_coordination"],
        can_notify=True,
    )

    return MainPMAgent(config)
