"""
Developer Agent Factories

Factory functions for creating developer agents for each team.
"""

from roboco.agents.developer import DeveloperAgent
from roboco.agents.factories._base import compose_prompt, make_slug
from roboco.models import AgentRole, Team
from roboco.models.agents import AgentConfig

# Default capabilities for each team
_CAPABILITIES = {
    Team.BACKEND: [
        "code_execution",
        "git_operations",
        "file_management",
        "api_development",
        "database_design",
    ],
    Team.FRONTEND: [
        "code_execution",
        "git_operations",
        "file_management",
        "browser_testing",
        "accessibility_testing",
        "responsive_design",
    ],
    Team.UX_UI: [
        "design_tools",
        "file_management",
        "figma_expertise",
        "prototyping",
        "design_system_management",
        "accessibility_design",
    ],
}


def _create_developer(
    name: str,
    team: Team,
    system_prompt: str | None = None,
) -> DeveloperAgent:
    """
    Internal factory for creating a developer agent.

    Args:
        name: Agent display name
        team: Team assignment
        system_prompt: Optional custom system prompt

    Returns:
        Configured DeveloperAgent instance
    """
    slug = make_slug(name)

    if system_prompt is None:
        system_prompt = compose_prompt(
            role=AgentRole.DEVELOPER,
            team=team,
            agent_slug=slug,
        )

    config = AgentConfig(
        name=name,
        slug=slug,
        role=AgentRole.DEVELOPER,
        team=team,
        system_prompt=system_prompt,
        capabilities=_CAPABILITIES[team],
    )

    return DeveloperAgent(config)


def create_backend_developer(
    name: str = "BE-Dev-1",
    system_prompt: str | None = None,
) -> DeveloperAgent:
    """Factory function to create a backend developer agent."""
    return _create_developer(name, Team.BACKEND, system_prompt)


def create_frontend_developer(
    name: str = "FE-Dev-1",
    system_prompt: str | None = None,
) -> DeveloperAgent:
    """Factory function to create a frontend developer agent."""
    return _create_developer(name, Team.FRONTEND, system_prompt)


def create_ux_developer(
    name: str = "UX-Dev-1",
    system_prompt: str | None = None,
) -> DeveloperAgent:
    """Factory function to create a UX/UI developer agent."""
    return _create_developer(name, Team.UX_UI, system_prompt)
