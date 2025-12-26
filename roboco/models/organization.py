"""
Organization Models

Defines organizational structures: Cell, Board, Organization.
"""

from typing import Any

from pydantic import BaseModel

from roboco.models import Team


class Cell(BaseModel):
    """A cell in the organization (backend, frontend, ux_ui)."""

    name: str
    team: Team
    pm: Any  # Agent
    developers: list[Any] = []  # List of Agent
    qa: Any | None = None  # Agent
    documenter: Any | None = None  # Agent

    model_config = {"arbitrary_types_allowed": True}


class Board(BaseModel):
    """The board of the organization."""

    product_owner: Any  # Agent
    head_marketing: Any  # Agent
    auditor: Any  # Agent
    main_pm: Any  # Agent

    model_config = {"arbitrary_types_allowed": True}


class Organization(BaseModel):
    """The complete organization structure."""

    board: Board
    cells: dict[str, Cell] = {}

    model_config = {"arbitrary_types_allowed": True}
