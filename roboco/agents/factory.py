"""
Agent Factory (Backwards Compatibility)

This module re-exports all factories from the new location.
Use roboco.agents.factories instead for new code.
"""

# Re-export everything from the new factories package
from roboco.agents.factories import (
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

__all__ = [
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
