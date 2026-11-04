"""Agent overview: one read model joining agent status, current task, spend.

Composes the agents table (status + current task), per-agent spend from
``UsageService.get_by_agent`` and the live provider rate-limit state, so a
status view needs one call instead of three.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from roboco.db.tables import AgentTable
from roboco.models.base import AgentStatus, Team
from roboco.services.base import BaseService
from roboco.services.gateway.rate_limit_tracker import RateLimitStateTracker
from roboco.services.usage import UsageService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _agent_item(agent: AgentTable, spend: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": agent.slug,
        "name": agent.name,
        "role": str(agent.role),
        "team": _opt_str(agent.team),
        "status": str(agent.status or AgentStatus.IDLE),
        "current_task_id": _opt_str(agent.current_task_id),
        "total_tokens": spend.get("total_tokens", 0),
        "cost_usd": spend.get("cost_usd", 0.0),
    }


class AgentOverviewService(BaseService):
    """Aggregate status + spend across all agents."""

    async def overview(
        self, period: str = "24h", team: Team | None = None
    ) -> dict[str, Any]:
        query = select(AgentTable).order_by(AgentTable.slug)
        if team:
            query = query.where(AgentTable.team == team)
        agents = (await self.session.execute(query)).scalars().all()
        spend = {
            row["agent_slug"]: row
            for row in await UsageService(self.session).get_by_agent(period)
        }
        items = [_agent_item(a, spend.get(a.slug, {})) for a in agents]
        counts = Counter(i["status"] for i in items)
        by_status = {s.value: counts[s.value] for s in AgentStatus}
        limited = await RateLimitStateTracker.list_rate_limited_providers()
        return {
            "period": period,
            "total": len(items),
            "by_status": by_status,
            "total_cost_usd": round(sum(i["cost_usd"] for i in items), 6),
            "rate_limited_providers": [p for p, _ in limited],
            "agents": items,
        }


def get_agent_overview_service(db: AsyncSession) -> AgentOverviewService:
    """Get an AgentOverviewService instance."""
    return AgentOverviewService(db)
