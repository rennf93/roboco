"""
Board Agent Factories

Factory functions for creating board-level agents
(Product Owner, Head of Marketing, Auditor).
"""

from roboco.agents.board import AuditorAgent, HeadMarketingAgent, ProductOwnerAgent
from roboco.agents.factories._base import compose_prompt
from roboco.models import AgentRole, Team
from roboco.models.agents import AgentConfig


def create_product_owner(
    name: str = "Product Owner",
    system_prompt: str | None = None,
) -> ProductOwnerAgent:
    """Factory function to create the Product Owner agent."""
    slug = "product-owner"

    if system_prompt is None:
        system_prompt = compose_prompt(
            role=AgentRole.PRODUCT_OWNER,
            team=None,
            agent_slug=slug,
        )

    config = AgentConfig(
        name=name,
        slug=slug,
        role=AgentRole.PRODUCT_OWNER,
        team=Team.BOARD,
        system_prompt=system_prompt,
        capabilities=["requirements", "prioritization", "acceptance"],
        can_notify=True,
    )

    return ProductOwnerAgent(config)


def create_head_marketing(
    name: str = "Head of Marketing",
    system_prompt: str | None = None,
) -> HeadMarketingAgent:
    """Factory function to create the Head of Marketing agent."""
    slug = "head-marketing"

    if system_prompt is None:
        system_prompt = compose_prompt(
            role=AgentRole.HEAD_MARKETING,
            team=None,
            agent_slug=slug,
        )

    config = AgentConfig(
        name=name,
        slug=slug,
        role=AgentRole.HEAD_MARKETING,
        team=Team.BOARD,
        system_prompt=system_prompt,
        capabilities=["marketing", "campaigns", "analytics"],
        can_notify=True,
    )

    return HeadMarketingAgent(config)


def create_auditor(
    name: str = "Auditor",
    system_prompt: str | None = None,
) -> AuditorAgent:
    """Factory function to create the Auditor agent."""
    slug = "auditor"

    if system_prompt is None:
        system_prompt = compose_prompt(
            role=AgentRole.AUDITOR,
            team=None,
            agent_slug=slug,
        )

    config = AgentConfig(
        name=name,
        slug=slug,
        role=AgentRole.AUDITOR,
        team=Team.BOARD,
        system_prompt=system_prompt,
        capabilities=["observation", "analysis", "audit", "ceo_reporting"],
        can_notify=True,
    )

    return AuditorAgent(config)
