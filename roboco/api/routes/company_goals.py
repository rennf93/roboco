"""Company-goals API — the CEO-owned company charter.

GET is open to any authenticated agent (the charter drives every agent's
briefing); PUT is CEO-only. Writes commit explicitly — get_db auto-commit is
unreliable under BaseHTTPMiddleware.
"""

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.company_goals import CompanyGoalsResponse, CompanyGoalsUpdate
from roboco.models import AgentRole
from roboco.services.company_goals import get_company_goals_service

router = APIRouter()


@router.get("", response_model=CompanyGoalsResponse)
async def get_company_goals(
    db: DbSession, agent: CurrentAgentContext
) -> CompanyGoalsResponse:
    """Return the company charter (any authenticated agent)."""
    _ = agent  # authentication only
    goals = await get_company_goals_service(db).get()
    return CompanyGoalsResponse(**goals)


@router.put("", response_model=CompanyGoalsResponse)
async def update_company_goals(
    data: CompanyGoalsUpdate, db: DbSession, agent: CurrentAgentContext
) -> CompanyGoalsResponse:
    """Update the company charter (CEO-only); only provided fields change."""
    if agent.role != AgentRole.CEO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the CEO can update company goals.",
        )
    service = get_company_goals_service(db)
    goals = await service.upsert(
        data.model_dump(exclude_unset=True), updated_by=agent.agent_id
    )
    await db.commit()
    return CompanyGoalsResponse(**goals)
