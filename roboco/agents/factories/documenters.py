"""
Documenter Agent Factories

Factory functions for creating documenter agents for each team.
"""

from roboco.agents.documenter import DocumenterAgent
from roboco.agents.factories._base import load_blueprint_prompt, make_slug
from roboco.models import AgentRole, Team
from roboco.models.agents import AgentConfig

# Blueprint paths for each team
_BLUEPRINTS = {
    Team.BACKEND: "agents/blueprints/backend/be-documenter.md",
    Team.FRONTEND: "agents/blueprints/frontend/fe-documenter.md",
    Team.UX_UI: "agents/blueprints/ux_ui/ux-documenter.md",
}

# Default prompts for each team
_DEFAULT_PROMPTS = {
    Team.BACKEND: "You are a backend documenter.",
    Team.FRONTEND: "You are a frontend documenter.",
    Team.UX_UI: "You are a UX/UI documenter.",
}

# Default capabilities for each team (matches blueprint capabilities)
_CAPABILITIES = {
    Team.BACKEND: [
        "technical_writing",
        "api_documentation",
        "code_reading",
        "file_management",
    ],
    Team.FRONTEND: [
        "technical_writing",
        "component_documentation",
        "code_reading",
        "storybook",
        "file_management",
    ],
    Team.UX_UI: [
        "design_documentation",
        "design_system_maintenance",
        "technical_writing",
        "file_management",
    ],
}


def _create_documenter(
    name: str,
    team: Team,
    system_prompt: str | None = None,
) -> DocumenterAgent:
    """
    Internal factory for creating a documenter agent.

    Args:
        name: Agent display name
        team: Team assignment
        system_prompt: Optional custom system prompt

    Returns:
        Configured DocumenterAgent instance
    """
    if system_prompt is None:
        system_prompt = load_blueprint_prompt(
            _BLUEPRINTS[team],
            _DEFAULT_PROMPTS[team],
        )

    config = AgentConfig(
        name=name,
        slug=make_slug(name),
        role=AgentRole.DOCUMENTER,
        team=team,
        system_prompt=system_prompt,
        capabilities=_CAPABILITIES[team],
    )

    return DocumenterAgent(config)


def create_backend_documenter(
    name: str = "BE-Documenter",
    system_prompt: str | None = None,
) -> DocumenterAgent:
    """Factory function to create a backend documenter agent."""
    return _create_documenter(name, Team.BACKEND, system_prompt)


def create_frontend_documenter(
    name: str = "FE-Documenter",
    system_prompt: str | None = None,
) -> DocumenterAgent:
    """Factory function to create a frontend documenter agent."""
    return _create_documenter(name, Team.FRONTEND, system_prompt)


def create_ux_documenter(
    name: str = "UX-Documenter",
    system_prompt: str | None = None,
) -> DocumenterAgent:
    """Factory function to create a UX/UI documenter agent."""
    return _create_documenter(name, Team.UX_UI, system_prompt)
