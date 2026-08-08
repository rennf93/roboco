"""
Telegram Route Helpers

Route-glue + app-wiring helpers backing roboco/api/routes/telegram.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.api.auth.login_limit import LoginRateLimiter
from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.config import settings

if TYPE_CHECKING:
    from fastapi import FastAPI


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="manage Telegram credentials")


def mount_telegram_miniapp_auth(app: FastAPI, prefix: str) -> None:
    """Mount ``POST {prefix}/webapp-auth`` only when the Mini App switch AND
    cloud auth are both armed — mirrors ``mount_cloud_auth``'s conditional
    mount. Off (either flag): the route doesn't exist at all."""
    if not (settings.telegram_miniapp_enabled and settings.cloud_auth_enabled):
        return
    # Deferred import: routes.telegram imports _require_ceo from this module,
    # so importing webapp_auth_router at module level here would cycle.
    from roboco.api.routes.telegram import webapp_auth_router

    app.include_router(webapp_auth_router, prefix=prefix, tags=["Telegram"])
    # Unconditional per-IP limiter, same backstop /auth/login gets: the guard
    # middleware's rate_limit decorator only bites when ROBOCO_GUARD_ENABLED
    # is on, which is toggled independently — a public session-minting route
    # must not depend on that coupling.
    app.add_middleware(
        LoginRateLimiter,
        paths=(f"{prefix}/webapp-auth",),
        max_attempts=settings.login_max_attempts,
        window=60,
    )
