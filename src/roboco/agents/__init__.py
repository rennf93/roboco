"""
RoboCo Agent Framework

Base classes, role-specific agents, and orchestration.
Phase 4: All 17 agent types implemented.
"""

from roboco.agents.base import Agent, AgentConfig, AgentState
from roboco.agents.orchestrator import Orchestrator

# Role-specific agents
from roboco.agents.developer import (
    DeveloperAgent,
    create_backend_developer,
    create_frontend_developer,
    create_ux_developer,
)
from roboco.agents.qa import (
    QAAgent,
    create_backend_qa,
    create_frontend_qa,
    create_ux_qa,
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
    create_ux_pm,
    create_main_pm,
)
from roboco.agents.board import (
    ProductOwnerAgent,
    HeadMarketingAgent,
    AuditorAgent,
    create_product_owner,
    create_head_marketing,
    create_auditor,
)

# Factory and deployment
from roboco.agents.factory import (
    Cell,
    Board,
    Organization,
    create_backend_cell,
    create_frontend_cell,
    create_ux_cell,
    create_board,
    create_organization,
    get_agent_roster,
    print_org_chart,
)

__all__ = [
    # Base
    "Agent",
    "AgentConfig",
    "AgentState",
    "Orchestrator",
    # Developers
    "DeveloperAgent",
    "create_backend_developer",
    "create_frontend_developer",
    "create_ux_developer",
    # QA
    "QAAgent",
    "create_backend_qa",
    "create_frontend_qa",
    "create_ux_qa",
    # Documenters
    "DocumenterAgent",
    "create_backend_documenter",
    "create_frontend_documenter",
    "create_ux_documenter",
    # PMs
    "CellPMAgent",
    "MainPMAgent",
    "create_backend_pm",
    "create_frontend_pm",
    "create_ux_pm",
    "create_main_pm",
    # Board
    "ProductOwnerAgent",
    "HeadMarketingAgent",
    "AuditorAgent",
    "create_product_owner",
    "create_head_marketing",
    "create_auditor",
    # Factory
    "Cell",
    "Board",
    "Organization",
    "create_backend_cell",
    "create_frontend_cell",
    "create_ux_cell",
    "create_board",
    "create_organization",
    "get_agent_roster",
    "print_org_chart",
]
