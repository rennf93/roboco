"""Telegram notifications bridge API — CEO-managed credentials (write-only).

The bridge itself is a server-side fan-out from the CEO-notify producers; the
only surface here is the credentials card. CEO-only; credentials are
write-only (the API never returns plaintext, mirroring ``/x/credentials``).
"""

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.telegram import (
    TelegramCredentialsSetRequest,
    TelegramCredentialsStatus,
)
from roboco.security import guard_deco
from roboco.services.telegram_credentials import (
    TelegramCredentialsValidationError,
    get_telegram_credentials_service,
)

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="manage Telegram credentials")


@router.get("/credentials", response_model=TelegramCredentialsStatus)
async def get_telegram_credentials(
    db: DbSession, agent: CurrentAgentContext
) -> TelegramCredentialsStatus:
    """Whether the bot token + chat id are stored. Never the secrets."""
    _require_ceo(agent)
    has_creds = await get_telegram_credentials_service(db).has_credentials()
    return TelegramCredentialsStatus(has_credentials=has_creds)


@router.post("/credentials", response_model=TelegramCredentialsStatus)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=8192)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.usage_monitor(max_calls=30, window=3600)
async def set_telegram_credentials(
    data: TelegramCredentialsSetRequest, db: DbSession, agent: CurrentAgentContext
) -> TelegramCredentialsStatus:
    """Set (or, passing both empty, clear) the bot token + chat id together."""
    _require_ceo(agent)
    svc = get_telegram_credentials_service(db)
    try:
        has_creds = await svc.set_credentials(
            bot_token=data.bot_token,
            chat_id=data.chat_id,
        )
    except TelegramCredentialsValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    await db.commit()
    return TelegramCredentialsStatus(has_credentials=has_creds)
