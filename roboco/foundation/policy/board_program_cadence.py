"""Pure cadence-slowing policy for Board Programs (no DB, no IO).

A program is SLOWED when all three hold:

1. sample: ``items_proposed >= min_proposals`` (below the sample a program is
   never slowed — too little evidence to judge its approval rate);
2. cost: ``cost_per_cycle_usd >= cost_threshold_usd`` (inclusive boundary);
3. unjustified: the approval rate does not justify that cost. The rule is cost
   per approved proposal = ``cost_per_cycle / approved_per_cycle`` where
   ``approved_per_cycle = items_approved / cycles_counted`` (both over the
   cost-known cycles only, so approvals of cost-unknown cycles never inflate
   it); the cost is
   justified only while that figure stays BELOW ``cost_threshold_usd``. A
   program with zero approvals has an infinite cost per approved proposal.

A slowdown only ever stretches the interval (``max(base, base * multiplier)``).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf


@dataclass(frozen=True)
class CadenceLimits:
    """Operator-configured limits the policy judges a program against."""

    min_proposals: int
    cost_threshold_usd: float


@dataclass(frozen=True)
class SlowdownDecision:
    """Outcome of ``decide_slowdown`` with every number that fed it."""

    slowed: bool
    sample_size: int
    min_proposals: int
    cost_per_cycle_usd: float | None
    cost_threshold_usd: float
    cost_per_approved_usd: float | None
    reason: str


def cost_per_approved(
    cost_per_cycle_usd: float, items_approved: int, cycles_counted: int
) -> float:
    """Cycle cost divided by approved proposals per cycle (``inf`` if none)."""
    if items_approved <= 0 or cycles_counted <= 0:
        return inf
    return cost_per_cycle_usd / (items_approved / cycles_counted)


def decide_slowdown(
    *,
    cost_per_cycle_usd: float | None,
    items_proposed: int,
    items_approved: int,
    cycles_counted: int,
    limits: CadenceLimits,
) -> SlowdownDecision:
    """Apply the three-part rule in the module docstring."""
    threshold = limits.cost_threshold_usd

    def _result(
        slowed: bool, reason: str, per_approved: float | None = None
    ) -> SlowdownDecision:
        return SlowdownDecision(
            slowed=slowed,
            sample_size=items_proposed,
            min_proposals=limits.min_proposals,
            cost_per_cycle_usd=cost_per_cycle_usd,
            cost_threshold_usd=threshold,
            cost_per_approved_usd=per_approved,
            reason=reason,
        )

    if items_proposed < limits.min_proposals:
        return _result(False, "below minimum proposals sample")
    if cost_per_cycle_usd is None or cost_per_cycle_usd < threshold:
        return _result(False, "cycle cost below threshold")
    per_approved = cost_per_approved(cost_per_cycle_usd, items_approved, cycles_counted)
    if per_approved < threshold:
        return _result(False, "approval rate justifies cycle cost", per_approved)
    return _result(
        True,
        "approval rate does not justify cycle cost",
        None if per_approved == inf else per_approved,
    )


def effective_interval_seconds(
    base_seconds: int, *, slowed: bool, multiplier: float
) -> int:
    """``base_seconds`` stretched by ``multiplier`` when slowed; never shorter."""
    if not slowed:
        return base_seconds
    return max(base_seconds, int(base_seconds * multiplier))
