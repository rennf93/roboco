"""Telegram notifications bridge API — CEO-managed credentials (write-only)
plus the Mini App sign-in exchange.

The bridge itself is a server-side fan-out from the CEO-notify producers; the
credentials card is CEO-only, write-only (the API never returns plaintext,
mirroring ``/x/credentials``). ``webapp_auth_router`` is a separate PUBLIC,
pre-auth router mounted only when both ``telegram_miniapp_enabled`` and
``cloud_auth_enabled`` are armed (see ``mount_telegram_miniapp_auth``,
mirroring ``roboco.api.auth.routes.mount_cloud_auth``'s conditional mount) —
its own signed ``initData`` is the authentication, not an agent/session header.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from roboco.api.auth.backend import cookie_transport, get_jwt_strategy
from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.telegram import (
    TelegramCredentialsSetRequest,
    TelegramCredentialsStatus,
    TelegramWebAppAuthRequest,
)
from roboco.config import settings
from roboco.db.tables import UserTable
from roboco.logging import get_logger
from roboco.security import guard_deco
from roboco.services.audit import get_audit_service
from roboco.services.telegram_credentials import (
    TelegramCredentialsValidationError,
    get_telegram_credentials_service,
)
from roboco.utils.telegram_initdata import validate_init_data

if TYPE_CHECKING:
    from fastapi import FastAPI

_logger = get_logger(__name__)

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


# ==========================================================================
# Mini App sign-in — public, pre-auth. Conditionally mounted; see
# ``mount_telegram_miniapp_auth`` below.
# ==========================================================================

webapp_auth_router = APIRouter()


@webapp_auth_router.post("/webapp-auth")
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=8192)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.usage_monitor(max_calls=30, window=3600)
async def webapp_auth(
    data: TelegramWebAppAuthRequest, db: DbSession, response: Response
) -> dict[str, bool]:
    """Exchange a validated Telegram Mini App ``initData`` for the same
    cloud-auth session cookie ``/api/auth/login`` issues.

    Refuses (no detail leak beyond "not configured"/"not authorized") unless:
    credentials are stored, the HMAC signature verifies, ``auth_date`` is
    fresh, and the initData's ``user.id`` matches the CEO's own stored
    ``chat_id`` — the same single-CEO trust anchor
    ``telegram_inbound._authorized_chat`` uses for the bot's inbound commands.
    """
    creds = await get_telegram_credentials_service(db).get_decrypted()
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram Mini App sign-in is not configured",
        )

    parsed = validate_init_data(
        data.init_data,
        creds.bot_token,
        settings.telegram_initdata_max_age_seconds,
    )
    if parsed is None:
        _logger.warning("Telegram Mini App auth: invalid or expired initData")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram sign-in data",
        )

    user_field = parsed.get("user")
    telegram_user_id = user_field.get("id") if isinstance(user_field, dict) else None
    if telegram_user_id is None or str(telegram_user_id) != str(creds.chat_id):
        _logger.warning(
            "Telegram Mini App auth: user id does not match the configured chat id"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized"
        )

    # The single seeded CEO login user — same lookup convention as
    # roboco.api.auth.seed.ensure_seed_user (ordered by the primary key's
    # text label, not UserTable.id, so mypy's TYPE_CHECKING split still sees
    # a column expression).
    user = (
        await db.execute(select(UserTable).order_by("id").limit(1))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No cloud-auth user is seeded",
        )

    token = await get_jwt_strategy().write_token(user)
    cookie_transport._set_login_cookie(response, token)

    await get_audit_service().log_event(
        event_type="telegram.webapp.login",
        details={"via": "telegram_miniapp"},
        severity="info",
    )

    return {"ok": True}


def mount_telegram_miniapp_auth(app: FastAPI, prefix: str) -> None:
    """Mount ``POST {prefix}/webapp-auth`` only when the Mini App switch AND
    cloud auth are both armed — mirrors ``mount_cloud_auth``'s conditional
    mount. Off (either flag): the route doesn't exist at all."""
    if not (settings.telegram_miniapp_enabled and settings.cloud_auth_enabled):
        return
    app.include_router(webapp_auth_router, prefix=prefix, tags=["Telegram"])
