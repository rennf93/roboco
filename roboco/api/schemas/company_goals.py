"""Company-goals API schemas — the CEO-owned company charter."""

from typing import Any

from pydantic import BaseModel, Field


class CompanyGoalsResponse(BaseModel):
    """The company charter as returned to any authenticated agent."""

    north_star: str
    objectives: list[dict[str, Any]]
    constraints: list[str]
    operating_policy: dict[str, Any]
    updated_at: str | None = None
    updated_by: str | None = None


class CompanyGoalsUpdate(BaseModel):
    """Partial update to the charter (CEO-only); only provided fields change."""

    north_star: str | None = Field(default=None)
    objectives: list[dict[str, Any]] | None = Field(default=None)
    constraints: list[str] | None = Field(default=None)
    operating_policy: dict[str, Any] | None = Field(default=None)
