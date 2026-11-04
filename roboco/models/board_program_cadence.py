"""Result model for the per-program cost-per-cycle vs approval-rate metric."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProgramCadenceMetric:
    """One Board Program's cycle cost next to its proposal approval rate.

    ``cycles_counted`` is the cycles whose exploration task still exists (their
    cost is known); ``cycles_missing_cost`` is the cycles whose
    ``exploration_task_id`` is null (task deleted), excluded from the cost mean.
    ``cost_per_cycle_usd`` is None when no cycle has a known cost, and
    ``approval_rate`` is None when nothing was proposed.
    """

    program_key: str
    cycles_counted: int
    cycles_missing_cost: int
    total_cost_usd: float
    cost_per_cycle_usd: float | None
    items_proposed: int
    items_approved: int
    approval_rate: float | None
