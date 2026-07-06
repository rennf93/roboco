"""Cloud-auth HTTP surface.

``/auth/status`` is always mounted (public, no auth) so the panel's
middleware can probe it before every navigation. Login/logout (FastAPI
Users' own router — no registration route) are mounted only when
cloud_auth_enabled, mirroring roboco.security.apply_guard's conditional
mount: off means these two routes don't exist at all.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, FastAPI
from fastapi_users import FastAPIUsers

from roboco.api.auth.backend import auth_backend
from roboco.api.auth.login_limit import LoginRateLimiter
from roboco.api.auth.manager import get_user_manager
from roboco.config import settings
from roboco.db.tables import UserTable

fastapi_users_app = FastAPIUsers[UserTable, UUID](get_user_manager, [auth_backend])

status_router = APIRouter()


@status_router.get("/status")
async def auth_status() -> dict[str, bool]:
    """Always-available probe; the panel's middleware gates on this."""
    return {"cloud_auth_enabled": settings.cloud_auth_enabled}


def mount_cloud_auth(app: FastAPI, prefix: str) -> None:
    """Mount the status probe unconditionally; login/logout only when armed."""
    app.include_router(status_router, prefix=prefix, tags=["Auth"])
    if not settings.cloud_auth_enabled:
        return
    app.include_router(
        fastapi_users_app.get_auth_router(auth_backend), prefix=prefix, tags=["Auth"]
    )
    app.add_middleware(
        LoginRateLimiter,
        prefix=prefix,
        max_attempts=settings.login_max_attempts,
        window=60,
    )
