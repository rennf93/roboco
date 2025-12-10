"""
Agent Factory and Cell Deployment

Provides factory functions for creating all agent types and
deploying complete cells with their full agent complement.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from roboco.agents.base import Agent
from roboco.agents.board import (
    AuditorAgent,
    HeadMarketingAgent,
    ProductOwnerAgent,
    create_auditor,
    create_head_marketing,
    create_product_owner,
)
from roboco.agents.developer import (
    DeveloperAgent,
    create_backend_developer,
    create_frontend_developer,
    create_ux_developer,
)
from roboco.agents.documenter import (
    DocumenterAgent,
    create_backend_documenter,
    create_frontend_documenter,
    create_ux_documenter,
)
from roboco.agents.pm import (
    CellPMAgent,
    MainPMAgent,
    create_backend_pm,
    create_frontend_pm,
    create_main_pm,
    create_ux_pm,
)
from roboco.agents.qa import (
    QAAgent,
    create_backend_qa,
    create_frontend_qa,
    create_ux_qa,
)
from roboco.models import Team

logger = structlog.get_logger()


@dataclass
class Cell:
    """A complete cell with all its agents."""

    name: str
    team: Team
    pm: CellPMAgent
    developers: list[DeveloperAgent]
    qa: QAAgent
    documenter: DocumenterAgent

    @property
    def all_agents(self) -> list[Agent]:
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

    product_owner: ProductOwnerAgent
    head_marketing: HeadMarketingAgent
    auditor: AuditorAgent

    @property
    def all_agents(self) -> list[Agent]:
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
    main_pm: MainPMAgent
    backend_cell: Cell
    frontend_cell: Cell
    ux_cell: Cell

    @property
    def all_agents(self) -> list[Agent]:
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

    def get_agent_by_id(self, agent_id: UUID) -> Agent | None:
        """Find an agent by ID."""
        for agent in self.all_agents:
            if agent.id == agent_id:
                return agent
        return None

    def get_agent_by_slug(self, slug: str) -> Agent | None:
        """Find an agent by slug."""
        for agent in self.all_agents:
            if agent.config.slug == slug:
                return agent
        return None

    def get_agents_by_team(self, team: Team) -> list[Agent]:
        """Get all agents in a team."""
        return [a for a in self.all_agents if a.team == team]


# =============================================================================
# CELL FACTORIES
# =============================================================================


def create_backend_cell() -> Cell:
    """
    Create a complete Backend cell.

    Includes:
    - 1 PM (BE-PM)
    - 2 Developers (BE-Dev-1, BE-Dev-2)
    - 1 QA (BE-QA)
    - 1 Documenter (BE-Documenter)
    """
    return Cell(
        name="backend-cell",
        team=Team.BACKEND,
        pm=create_backend_pm(),
        developers=[
            create_backend_developer("BE-Dev-1"),
            create_backend_developer("BE-Dev-2"),
        ],
        qa=create_backend_qa(),
        documenter=create_backend_documenter(),
    )


def create_frontend_cell() -> Cell:
    """
    Create a complete Frontend cell.

    Includes:
    - 1 PM (FE-PM)
    - 2 Developers (FE-Dev-1, FE-Dev-2)
    - 1 QA (FE-QA)
    - 1 Documenter (FE-Documenter)
    """
    return Cell(
        name="frontend-cell",
        team=Team.FRONTEND,
        pm=create_frontend_pm(),
        developers=[
            create_frontend_developer("FE-Dev-1"),
            create_frontend_developer("FE-Dev-2"),
        ],
        qa=create_frontend_qa(),
        documenter=create_frontend_documenter(),
    )


def create_ux_cell() -> Cell:
    """
    Create a complete UX/UI cell.

    Includes:
    - 1 PM (UX-PM)
    - 1 Developer (UX-Dev)
    - 1 QA (UX-QA)
    - 1 Documenter (UX-Documenter)
    """
    return Cell(
        name="uxui-cell",
        team=Team.UX_UI,
        pm=create_ux_pm(),
        developers=[
            create_ux_developer("UX-Dev"),
        ],
        qa=create_ux_qa(),
        documenter=create_ux_documenter(),
    )


def create_board() -> Board:
    """
    Create the Board level.

    Includes:
    - Product Owner
    - Head of Marketing
    - Auditor
    """
    return Board(
        product_owner=create_product_owner(),
        head_marketing=create_head_marketing(),
        auditor=create_auditor(),
    )


def create_organization() -> Organization:
    """
    Create the complete AI organization.

    Total: 18 AI agents
    - Board: 3 (Product Owner, Head of Marketing, Auditor)
    - Management: 1 (Main PM)
    - Backend Cell: 5 (PM, 2 Devs, QA, Documenter)
    - Frontend Cell: 5 (PM, 2 Devs, QA, Documenter)
    - UX/UI Cell: 4 (PM, 1 Dev, QA, Documenter)
    """
    return Organization(
        board=create_board(),
        main_pm=create_main_pm(),
        backend_cell=create_backend_cell(),
        frontend_cell=create_frontend_cell(),
        ux_cell=create_ux_cell(),
    )


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def get_agent_roster() -> dict[str, list[dict[str, Any]]]:
    """
    Get a roster of all agents without instantiating them.

    Useful for displaying the org structure.
    """
    return {
        "board": [
            {"name": "Product Owner", "role": "product_owner", "slug": "product-owner"},
            {
                "name": "Head of Marketing",
                "role": "head_marketing",
                "slug": "head-marketing",
            },
            {"name": "Auditor", "role": "auditor", "slug": "auditor"},
        ],
        "management": [
            {"name": "Main PM", "role": "main_pm", "slug": "main-pm"},
        ],
        "backend_cell": [
            {"name": "BE-PM", "role": "cell_pm", "slug": "be-pm"},
            {"name": "BE-Dev-1", "role": "developer", "slug": "be-dev-1"},
            {"name": "BE-Dev-2", "role": "developer", "slug": "be-dev-2"},
            {"name": "BE-QA", "role": "qa", "slug": "be-qa"},
            {"name": "BE-Documenter", "role": "documenter", "slug": "be-documenter"},
        ],
        "frontend_cell": [
            {"name": "FE-PM", "role": "cell_pm", "slug": "fe-pm"},
            {"name": "FE-Dev-1", "role": "developer", "slug": "fe-dev-1"},
            {"name": "FE-Dev-2", "role": "developer", "slug": "fe-dev-2"},
            {"name": "FE-QA", "role": "qa", "slug": "fe-qa"},
            {"name": "FE-Documenter", "role": "documenter", "slug": "fe-documenter"},
        ],
        "ux_cell": [
            {"name": "UX-PM", "role": "cell_pm", "slug": "ux-pm"},
            {"name": "UX-Dev", "role": "developer", "slug": "ux-dev"},
            {"name": "UX-QA", "role": "qa", "slug": "ux-qa"},
            {"name": "UX-Documenter", "role": "documenter", "slug": "ux-documenter"},
        ],
    }


def print_org_chart() -> str:
    """Generate a text-based org chart."""
    return """
                              ┌─────────────┐
                              │     CEO     │
                              │   (Human)   │
                              └──────┬──────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
              ┌─────▼─────┐    ┌─────▼─────┐    ┌─────▼─────┐
              │  Product  │    │   Head    │    │  Auditor  │
              │   Owner   │    │ Marketing │    │   (Spy)   │
              └─────┬─────┘    └─────┬─────┘    └───────────┘
                    │                │                ▲
                    └───────┬────────┘                │
                            │                    [observes all]
                     ┌──────▼──────┐
                     │   Main PM   │
                     └──────┬──────┘
                            │
       ┌────────────────────┼────────────────────┐
       │                    │                    │
 ┌─────▼─────┐        ┌─────▼─────┐        ┌─────▼─────┐
 │  BE-PM    │        │  FE-PM    │        │  UX-PM    │
 ├───────────┤        ├───────────┤        ├───────────┤
 │ BE-Dev x2 │        │ FE-Dev x2 │        │ UX-Dev    │
 │ BE-QA     │        │ FE-QA     │        │ UX-QA     │
 │ BE-Doc    │        │ FE-Doc    │        │ UX-Doc    │
 └───────────┘        └───────────┘        └───────────┘

 Total: 18 AI Agents + 1 Human CEO = 19 organization members
"""
