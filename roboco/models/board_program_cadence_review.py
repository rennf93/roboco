"""CEO cadence-review row: one Board Program's cost, approval and slowdown."""

from __future__ import annotations

from pydantic import BaseModel


class ProgramCadenceReview(BaseModel):
    """What the CEO sees per program, with the numbers behind any slowdown."""

    program_key: str
    cost_per_cycle_usd: float | None
    approval_rate: float | None
    sample_size: int
    items_approved: int
    slowed: bool
    slowdown_disabled: bool
    original_interval_seconds: int
    effective_interval_seconds: int
    reason: str
    cost_threshold_usd: float
    min_proposals: int
    slowdown_multiplier: float
    cost_per_approved_usd: float | None
