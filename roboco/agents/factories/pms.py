"""
PM Agent Factories

Factory functions for creating PM agents (Cell PMs and Main PM).
"""

from roboco.agents.factories._base import compose_prompt, make_slug
from roboco.agents.pm import CellPMAgent, MainPMAgent
from roboco.models import AgentRole, Team
from roboco.models.agents import AgentConfig


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
    slug = make_slug(name)

    if system_prompt is None:
        system_prompt = compose_prompt(
            role=AgentRole.CELL_PM,
            team=team,
            agent_slug=slug,
        )

    config = AgentConfig(
        name=name,
        slug=slug,
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
    slug = "main-pm"

    if system_prompt is None:
        system_prompt = compose_prompt(
            role=AgentRole.MAIN_PM,
            team=None,  # Main PM has no specific team
            agent_slug=slug,
        )

    config = AgentConfig(
        name=name,
        slug=slug,
        role=AgentRole.MAIN_PM,
        team=Team.BOARD,
        system_prompt=system_prompt,
        capabilities=["task_management", "notifications", "cross_cell_coordination"],
        can_notify=True,
    )

    return MainPMAgent(config)
