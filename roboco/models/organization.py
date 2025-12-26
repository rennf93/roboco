"""
Organization Models

Defines organizational structures: Cell, Board, Organization.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel

from roboco.models import Team

if TYPE_CHECKING:
    from roboco.agents.base import Agent


class Cell(BaseModel):
    """A cell in the organization (backend, frontend, ux_ui)."""

    name: str
    team: Team
    pm: "Agent"
    developers: list["Agent"] = []
    qa: "Agent | None" = None
    documenter: "Agent | None" = None

    model_config = {"arbitrary_types_allowed": True}


class Board(BaseModel):
    """The board of the organization (3 agents reporting to CEO)."""

    product_owner: "Agent"
    head_marketing: "Agent"
    auditor: "Agent"

    model_config = {"arbitrary_types_allowed": True}


class Organization(BaseModel):
    """The complete organization structure."""

    board: Board
    main_pm: "Agent"
    backend_cell: Cell
    frontend_cell: Cell
    ux_cell: Cell

    model_config = {"arbitrary_types_allowed": True}
