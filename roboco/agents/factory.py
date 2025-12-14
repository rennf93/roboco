"""
Agent Factory and Cell Deployment

Provides factory functions for creating all agent types and
deploying complete cells with their full agent complement.
"""

from typing import Any

from roboco.agents.board import (
    create_auditor,
    create_head_marketing,
    create_product_owner,
)
from roboco.agents.developer import (
    create_backend_developer,
    create_frontend_developer,
    create_ux_developer,
)
from roboco.agents.documenter import (
    create_backend_documenter,
    create_frontend_documenter,
    create_ux_documenter,
)
from roboco.agents.pm import (
    create_backend_pm,
    create_frontend_pm,
    create_main_pm,
    create_ux_pm,
)
from roboco.agents.qa import (
    create_backend_qa,
    create_frontend_qa,
    create_ux_qa,
)
from roboco.models import Team
from roboco.models.organization import Board, Cell, Organization

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
