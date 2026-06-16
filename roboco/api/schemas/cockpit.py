"""Cockpit API schemas — the CEO's read-only company summary."""

from typing import Any

from pydantic import BaseModel


class DeliverySummary(BaseModel):
    task_counts: dict[str, int]
    in_flight: int
    blocked: int
    awaiting_ceo: int


class SpendSummary(BaseModel):
    spend_30d_usd: float
    projected_monthly_usd: float | None = None
    monthly_budget_cap_usd: float | None = None
    over_budget: bool


class CockpitSignal(BaseModel):
    kind: str
    summary: str
    detail: str


class CockpitSummary(BaseModel):
    """Compact, honest-by-proxy snapshot of company state for the CEO."""

    basis: str
    north_star: str
    objectives: list[dict[str, Any]]
    delivery: DeliverySummary
    spend: SpendSummary
    pending_pitches: int
    signals: list[CockpitSignal]


class CockpitSignals(BaseModel):
    """Just the strategy-engine signals — the Dashboard panel's lightweight slice
    (avoids the full /summary fan-out: goals / usage / task-counts / pitches)."""

    signals: list[CockpitSignal]
