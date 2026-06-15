"""Cockpit API — the CEO's read-only "is the business winning?" summary."""

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.cockpit import CockpitSummary
from roboco.models import AgentRole
from roboco.services.cockpit import get_cockpit_service

router = APIRouter()

_COCKPIT_ROLES = frozenset(
    {
        AgentRole.CEO,
        AgentRole.PRODUCT_OWNER,
        AgentRole.HEAD_MARKETING,
        AgentRole.MAIN_PM,
        AgentRole.SECRETARY,
    }
)


@router.get("/summary", response_model=CockpitSummary)
async def cockpit_summary(db: DbSession, agent: CurrentAgentContext) -> CockpitSummary:
    """Read-only company snapshot (CEO, Board, Main PM, Secretary)."""
    if agent.role not in _COCKPIT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role '{agent.role}' may not view the cockpit",
        )
    return CockpitSummary(**await get_cockpit_service(db).summary())
