"""
Developer Agent Factories

Factory functions for creating developer agents for each team.
"""

from roboco.agents.developer import DeveloperAgent
from roboco.agents.factories._base import load_blueprint_prompt, make_slug
from roboco.models import AgentRole, Team
from roboco.models.agents import AgentConfig

# Blueprint paths for each team
_BLUEPRINTS = {
    Team.BACKEND: "agents/blueprints/backend/be-dev.md",
    Team.FRONTEND: "agents/blueprints/frontend/fe-dev.md",
    Team.UX_UI: "agents/blueprints/ux_ui/ux-dev.md",
}

# Default prompts for each team
_DEFAULT_PROMPTS = {
    Team.BACKEND: "You are a backend developer.",
    Team.FRONTEND: "You are a frontend developer.",
    Team.UX_UI: "You are a UX/UI developer.",
}

# Default capabilities for each team (matches blueprint capabilities)
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
    if system_prompt is None:
        system_prompt = load_blueprint_prompt(
            _BLUEPRINTS[team],
            _DEFAULT_PROMPTS[team],
        )

    config = AgentConfig(
        name=name,
        slug=make_slug(name),
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
