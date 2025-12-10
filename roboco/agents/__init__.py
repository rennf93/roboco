"""
RoboCo Agent Framework

Base classes, role-specific agents, and orchestration.
Phase 4: All 17 agent types implemented.
"""

from roboco.agents.base import Agent, AgentConfig, AgentState
from roboco.agents.board import (
    AuditorAgent,
    HeadMarketingAgent,
    ProductOwnerAgent,
    create_auditor,
    create_head_marketing,
    create_product_owner,
)

# Role-specific agents
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

# Factory and deployment
from roboco.agents.factory import (
    Board,
    Cell,
    Organization,
    create_backend_cell,
    create_board,
    create_frontend_cell,
    create_organization,
    create_ux_cell,
    get_agent_roster,
    print_org_chart,
)
from roboco.agents.orchestrator import Orchestrator
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

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentState",
    "AuditorAgent",
    "Board",
    "Cell",
    "CellPMAgent",
    "DeveloperAgent",
    "DocumenterAgent",
    "HeadMarketingAgent",
    "MainPMAgent",
    "Orchestrator",
    "Organization",
    "ProductOwnerAgent",
    "QAAgent",
    "create_auditor",
    "create_backend_cell",
    "create_backend_developer",
    "create_backend_documenter",
    "create_backend_pm",
    "create_backend_qa",
    "create_board",
    "create_frontend_cell",
    "create_frontend_developer",
    "create_frontend_documenter",
    "create_frontend_pm",
    "create_frontend_qa",
    "create_head_marketing",
    "create_main_pm",
    "create_organization",
    "create_product_owner",
    "create_ux_cell",
    "create_ux_developer",
    "create_ux_documenter",
    "create_ux_pm",
    "create_ux_qa",
    "get_agent_roster",
    "print_org_chart",
]
