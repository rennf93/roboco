"""Entire trails bridge API — the CEO's token (write-only), the bridge's
status, and the retry of operations that stopped retrying.

The token mirrors ``/telegram/credentials``: the API never returns it, only
``has_token``. The bridge itself runs in the orchestrator
(``roboco.services.entire_trails``).
"""

from __future__ import annotations

from fastapi import APIRouter

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.entire import (
    EntireTokenSetRequest,
    EntireTokenStatus,
    EntireTrailsRetryResponse,
    EntireTrailsStatusResponse,
)
from roboco.security import guard_deco
from roboco.services.entire_credentials import get_entire_credentials_service
from roboco.services.entire_trails.status import retry_dead, trails_status

router = APIRouter()


@router.get("/credentials", response_model=EntireTokenStatus)
async def get_entire_credentials(
    db: DbSession, agent: CurrentAgentContext
) -> EntireTokenStatus:
    """Whether the Entire token is stored. Never the token."""
    require_ceo_role(agent.role, action="manage the Entire token")
    return EntireTokenStatus(
        has_token=await get_entire_credentials_service(db).has_token()
    )


@router.post("/credentials", response_model=EntireTokenStatus)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=16384)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.usage_monitor(max_calls=30, window=3600)
async def set_entire_credentials(
    data: EntireTokenSetRequest, db: DbSession, agent: CurrentAgentContext
) -> EntireTokenStatus:
    """Set the Entire token, or clear it with an empty one."""
    require_ceo_role(agent.role, action="manage the Entire token")
    has_token = await get_entire_credentials_service(db).set_token(data.token)
    await db.commit()
    return EntireTokenStatus(has_token=has_token)


@router.get("/trails/status", response_model=EntireTrailsStatusResponse)
async def get_entire_trails_status(
    db: DbSession, agent: CurrentAgentContext
) -> EntireTrailsStatusResponse:
    """Whether the bridge can run, and what its outbox holds."""
    require_ceo_role(agent.role, action="view the Entire trails status")
    status = await trails_status(db)
    return EntireTrailsStatusResponse(
        enabled=status.enabled,
        has_token=status.has_token,
        cli_available=status.cli_available,
        open_trails=status.open_trails,
        pending=status.pending,
        dead=status.dead,
        last_error=status.last_error,
    )


@router.post("/trails/retry", response_model=EntireTrailsRetryResponse)
@guard_deco.rate_limit(requests=10, window=60)
async def retry_entire_trails(
    db: DbSession, agent: CurrentAgentContext
) -> EntireTrailsRetryResponse:
    """Put every operation that stopped retrying back in its queue."""
    require_ceo_role(agent.role, action="retry Entire trails operations")
    retried = await retry_dead(db)
    await db.commit()
    return EntireTrailsRetryResponse(retried=retried)
