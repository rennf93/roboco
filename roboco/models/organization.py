"""
Organization Models

Domain types for the organizational structure (cells, board, organization).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from roboco.models import Team

if TYPE_CHECKING:
    from roboco.agents.base import Agent
    from roboco.agents.board import (
        AuditorAgent,
        HeadMarketingAgent,
        ProductOwnerAgent,
    )
    from roboco.agents.developer import DeveloperAgent
    from roboco.agents.documenter import DocumenterAgent
    from roboco.agents.pm import CellPMAgent, MainPMAgent
    from roboco.agents.qa import QAAgent

logger = structlog.get_logger()


@dataclass
class Cell:
    """A complete cell with all its agents."""

    name: str
    team: Team
    pm: "CellPMAgent"
    developers: list["DeveloperAgent"]
    qa: "QAAgent"
    documenter: "DocumenterAgent"

    @property
    def all_agents(self) -> list["Agent"]:
        """Get all agents in the cell."""
        return [self.pm, *self.developers, self.qa, self.documenter]

    async def start_all(self) -> None:
        """Start all agents in the cell."""
        for agent in self.all_agents:
            await agent.start()
        logger.info("Cell started", cell=self.name, agents=len(self.all_agents))

    async def stop_all(self) -> None:
        """Stop all agents in the cell."""
        for agent in self.all_agents:
            await agent.stop()
        logger.info("Cell stopped", cell=self.name)


@dataclass
class Board:
    """The board level with all board agents."""

    product_owner: "ProductOwnerAgent"
    head_marketing: "HeadMarketingAgent"
    auditor: "AuditorAgent"

    @property
    def all_agents(self) -> list["Agent"]:
        """Get all board agents."""
        return [self.product_owner, self.head_marketing, self.auditor]

    async def start_all(self) -> None:
        """Start all board agents."""
        for agent in self.all_agents:
            await agent.start()
        logger.info("Board started", agents=len(self.all_agents))

    async def stop_all(self) -> None:
        """Stop all board agents."""
        for agent in self.all_agents:
            await agent.stop()
        logger.info("Board stopped")


@dataclass
class Organization:
    """The complete AI organization."""

    board: Board
    main_pm: "MainPMAgent"
    backend_cell: Cell
    frontend_cell: Cell
    ux_cell: Cell

    @property
    def all_agents(self) -> list["Agent"]:
        """Get all agents in the organization."""
        agents: list[Agent] = []
        agents.extend(self.board.all_agents)
        agents.append(self.main_pm)
        agents.extend(self.backend_cell.all_agents)
        agents.extend(self.frontend_cell.all_agents)
        agents.extend(self.ux_cell.all_agents)
        return agents

    @property
    def agent_count(self) -> int:
        """Total number of agents."""
        return len(self.all_agents)

    async def start_all(self) -> None:
        """Start the entire organization."""
        logger.info("Starting organization")

        # Start board first
        await self.board.start_all()
        await self.main_pm.start()

        # Then cells
        await self.backend_cell.start_all()
        await self.frontend_cell.start_all()
        await self.ux_cell.start_all()

        logger.info("Organization started", total_agents=self.agent_count)

    async def stop_all(self) -> None:
        """Stop the entire organization."""
        logger.info("Stopping organization")

        # Stop cells first
        await self.ux_cell.stop_all()
        await self.frontend_cell.stop_all()
        await self.backend_cell.stop_all()

        # Then management
        await self.main_pm.stop()
        await self.board.stop_all()

        logger.info("Organization stopped")

    def get_agent_by_id(self, agent_id: UUID) -> "Agent | None":
        """Find an agent by ID."""
        for agent in self.all_agents:
            if agent.id == agent_id:
                return agent
        return None

    def get_agent_by_slug(self, slug: str) -> "Agent | None":
        """Find an agent by slug."""
        for agent in self.all_agents:
            if agent.config.slug == slug:
                return agent
        return None

    def get_agents_by_team(self, team: Team) -> list["Agent"]:
        """Get all agents in a team."""
        return [a for a in self.all_agents if a.team == team]
