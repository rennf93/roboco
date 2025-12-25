"""
RoboCo Agent Framework

Base classes, role-specific agents, and orchestration.
Phase 4: All 17 agent types implemented.
"""

from roboco.agents.base import (
    Agent,
    AgentConfig,
    AgentState,
    set_reasoning_stream_callback,
)

# Agent implementations
from roboco.agents.board import AuditorAgent, HeadMarketingAgent, ProductOwnerAgent
from roboco.agents.developer import DeveloperAgent
from roboco.agents.documenter import DocumenterAgent

# Factory functions (from new factories/ module)
from roboco.agents.factories import (
    Board,
    Cell,
    Organization,
    create_auditor,
    create_backend_cell,
    create_backend_developer,
    create_backend_documenter,
    create_backend_pm,
    create_backend_qa,
    create_board,
    create_frontend_cell,
    create_frontend_developer,
    create_frontend_documenter,
    create_frontend_pm,
    create_frontend_qa,
    create_head_marketing,
    create_main_pm,
    create_organization,
    create_product_owner,
    create_ux_cell,
    create_ux_developer,
    create_ux_documenter,
    create_ux_pm,
    create_ux_qa,
    get_agent_roster,
    print_org_chart,
)

# Mixins for building agents
from roboco.agents.mixins import (
    BaseContext,
    ContextManager,
    CyclicPhaseConfig,
    CyclicPhaseRunner,
    PhaseConfig,
    PhaseEngine,
    PhaseResult,
    ProgressTracker,
    WorkFinder,
    WorkSearchStrategy,
)
from roboco.agents.orchestrator import Orchestrator
from roboco.agents.pm import CellPMAgent, MainPMAgent
from roboco.agents.qa import QAAgent

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentState",
    "AuditorAgent",
    "BaseContext",
    "Board",
    "Cell",
    "CellPMAgent",
    "ContextManager",
    "CyclicPhaseConfig",
    "CyclicPhaseRunner",
    "DeveloperAgent",
    "DocumenterAgent",
    "HeadMarketingAgent",
    "MainPMAgent",
    "Orchestrator",
    "Organization",
    "PhaseConfig",
    "PhaseEngine",
    "PhaseResult",
    "ProductOwnerAgent",
    "ProgressTracker",
    "QAAgent",
    "WorkFinder",
    "WorkSearchStrategy",
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
    "set_reasoning_stream_callback",
]
