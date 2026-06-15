"""CockpitService — the CEO's read-only "is the business winning?" summary.

A pure aggregation over existing data: the charter (goals), delivery counts,
30-day spend vs the charter's budget cap, pending pitches, and the strategy
engine's signals (what needs the CEO). Read-only; no writes, no side effects.

Performance is necessarily a **proxy** (work shipped, spend, signals) until the
CEO greenlights real external launches — every payload is stamped
``basis="proxy"`` so that boundary stays honest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.services.base import BaseService
from roboco.services.company_goals import get_company_goals_service
from roboco.services.pitch import get_pitch_service
from roboco.services.strategy_engine import get_strategy_engine
from roboco.services.task import get_task_service
from roboco.services.usage import get_usage_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CockpitService(BaseService):
    """Aggregate company state into one read-only cockpit summary."""

    service_name = "cockpit"

    async def summary(self) -> dict[str, Any]:
        goals = await get_company_goals_service(self.session).get()
        counts = await get_task_service(self.session).count_by_status()
        usage_svc = get_usage_service(self.session)
        spend = await usage_svc.get_summary("30d")
        projection = await usage_svc.get_projection()
        observations = await get_strategy_engine(self.session).assess()
        pitches = await get_pitch_service(self.session).list_pitches()

        operating_policy = goals.get("operating_policy") or {}
        budget_cap = _as_float(operating_policy.get("monthly_budget_cap"))
        spend_30d = _as_float(spend.get("total_cost_usd")) or 0.0

        return {
            "basis": "proxy",
            "north_star": goals.get("north_star", ""),
            "objectives": goals.get("objectives", []),
            "delivery": {
                "task_counts": counts,
                "in_flight": counts.get("in_progress", 0) + counts.get("claimed", 0),
                "blocked": counts.get("blocked", 0),
                "awaiting_ceo": counts.get("awaiting_ceo_approval", 0),
            },
            "spend": {
                "spend_30d_usd": round(spend_30d, 2),
                "projected_monthly_usd": projection.get("projected_monthly_cost_usd"),
                "monthly_budget_cap_usd": budget_cap,
                "over_budget": bool(budget_cap is not None and spend_30d > budget_cap),
            },
            "pending_pitches": sum(1 for p in pitches if p.status == "proposed"),
            "signals": [
                {"kind": o.kind, "summary": o.summary, "detail": o.detail}
                for o in observations
            ],
        }


def get_cockpit_service(session: AsyncSession) -> CockpitService:
    """Construct a CockpitService bound to ``session``."""
    return CockpitService(session)
