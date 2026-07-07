"""Cloud-auth HTTP surface.

``/auth/status`` is always mounted (public, no auth) so the panel's
middleware can probe it before every navigation. Login/logout (FastAPI
Users' own router — no registration route) are mounted only when
cloud_auth_enabled, mirroring roboco.security.apply_guard's conditional
mount: off means these two routes don't exist at all.
"""

from __future__ import annotations

import logging
import time
from uuid import UUID

import jwt
from fastapi import APIRouter, Cookie, FastAPI, Response
from fastapi_users import FastAPIUsers

from roboco.api.auth import revocation
from roboco.api.auth.backend import SESSION_COOKIE_NAME, auth_backend
from roboco.api.auth.login_limit import LoginRateLimiter
from roboco.api.auth.manager import get_user_manager
from roboco.config import settings
from roboco.db.tables import UserTable

fastapi_users_app = FastAPIUsers[UserTable, UUID](get_user_manager, [auth_backend])

_logger = logging.getLogger(__name__)

status_router = APIRouter()


@status_router.get("/status")
async def auth_status() -> dict[str, bool]:
    """Always-available probe; the panel's middleware gates on this."""
    return {"cloud_auth_enabled": settings.cloud_auth_enabled}


_logout_router = APIRouter()


@_logout_router.post("/logout")
async def revoke_and_logout(
    response: Response,
    cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, bool]:
    """Revoke the current session's jti, then delete the cookie.

    FastAPI Users' built-in logout only clears the cookie — a stolen copy
    stayed valid for ``cloud_auth_cookie_max_age``. This route reads the
    cookie's ``jti`` + ``exp``, adds the jti to the revocation set for the
    cookie's remaining life, then deletes the cookie. Fail-open: a Redis
    error during revocation still clears the cookie (the pwd_fp check
    remains the strong user-wide revocation).
    """
    if cookie:
        try:
            data = jwt.decode(
                cookie,
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_aud": False,
                },
            )
            jti = data.get("jti")
            exp = data.get("exp")
            if isinstance(jti, str) and isinstance(exp, (int, float)):
                await revocation.revoke_jti(jti, max(int(exp - time.time()), 1))
        except Exception:
            _logger.warning("logout jti revocation skipped", exc_info=True)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


def mount_cloud_auth(app: FastAPI, prefix: str) -> None:
    """Mount the status probe unconditionally; login/logout only when armed."""
    app.include_router(status_router, prefix=prefix, tags=["Auth"])
    if not settings.cloud_auth_enabled:
        return
    auth_router = fastapi_users_app.get_auth_router(auth_backend)
    # Drop the built-in logout so our jti-revoking /logout takes the path.
    # APIRouter.routes is typed list[BaseRoute]; the live objects are
    # starlette Route's with .path — getattr avoids the declared-type gap.
    auth_router.routes = [
        r for r in auth_router.routes if getattr(r, "path", "") != "/logout"
    ]
    app.include_router(auth_router, prefix=prefix, tags=["Auth"])
    app.include_router(_logout_router, prefix=prefix, tags=["Auth"])
    app.add_middleware(
        LoginRateLimiter,
        prefix=prefix,
        max_attempts=settings.login_max_attempts,
        window=60,
    )
