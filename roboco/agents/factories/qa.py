"""
QA Agent Factories

Factory functions for creating QA agents for each team.
"""

from roboco.agents.factories._base import compose_prompt, make_slug
from roboco.agents.qa import QAAgent
from roboco.models import AgentRole, Team
from roboco.models.agents import AgentConfig

# Default capabilities for each team
_CAPABILITIES = {
    Team.BACKEND: [
        "code_review",
        "test_execution",
        "security_analysis",
        "quality_assurance",
    ],
    Team.FRONTEND: [
        "visual_testing",
        "accessibility_testing",
        "browser_testing",
        "quality_assurance",
    ],
    Team.UX_UI: [
        "design_review",
        "accessibility_review",
        "quality_assurance",
    ],
}


def _create_qa(
    name: str,
    team: Team,
    system_prompt: str | None = None,
) -> QAAgent:
    """
    Internal factory for creating a QA agent.

    Args:
        name: Agent display name
        team: Team assignment
        system_prompt: Optional custom system prompt

    Returns:
        Configured QAAgent instance
    """
    slug = make_slug(name)

    if system_prompt is None:
        system_prompt = compose_prompt(
            role=AgentRole.QA,
            team=team,
            agent_slug=slug,
        )

    config = AgentConfig(
        name=name,
        slug=slug,
        role=AgentRole.QA,
        team=team,
        system_prompt=system_prompt,
        capabilities=_CAPABILITIES[team],
    )

    return QAAgent(config)


def create_backend_qa(
    name: str = "BE-QA",
    system_prompt: str | None = None,
) -> QAAgent:
    """Factory function to create a backend QA agent."""
    return _create_qa(name, Team.BACKEND, system_prompt)


def create_frontend_qa(
    name: str = "FE-QA",
    system_prompt: str | None = None,
) -> QAAgent:
    """Factory function to create a frontend QA agent."""
    return _create_qa(name, Team.FRONTEND, system_prompt)


def create_ux_qa(
    name: str = "UX-QA",
    system_prompt: str | None = None,
) -> QAAgent:
    """Factory function to create a UX/UI QA agent."""
    return _create_qa(name, Team.UX_UI, system_prompt)
