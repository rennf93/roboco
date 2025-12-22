"""
Agent Factories

Centralized factory functions for creating all agent types.

Modules:
- developers: Developer agent factories
- qa: QA agent factories
- documenters: Documenter agent factories
- pms: PM agent factories (Cell PMs and Main PM)
- board: Board agent factories (Product Owner, Head Marketing, Auditor)
- cells: Cell and Organization factories
"""

# Board agents
from roboco.agents.factories.board import (
    create_auditor,
    create_head_marketing,
    create_product_owner,
)

# Cell and organization
from roboco.agents.factories.cells import (
    create_backend_cell,
    create_board,
    create_frontend_cell,
    create_organization,
    create_ux_cell,
    get_agent_roster,
    print_org_chart,
)

# Developers
from roboco.agents.factories.developers import (
    create_backend_developer,
    create_frontend_developer,
    create_ux_developer,
)

# Documenters
from roboco.agents.factories.documenters import (
    create_backend_documenter,
    create_frontend_documenter,
    create_ux_documenter,
)

# PMs
from roboco.agents.factories.pms import (
    create_backend_pm,
    create_frontend_pm,
    create_main_pm,
    create_ux_pm,
)

# QA
from roboco.agents.factories.qa import (
    create_backend_qa,
    create_frontend_qa,
    create_ux_qa,
)

# Organization types (re-exported for convenience)
from roboco.models.organization import Board, Cell, Organization

__all__ = [
    "Board",
    "Cell",
    "Organization",
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
