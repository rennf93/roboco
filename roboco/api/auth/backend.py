"""Cookie transport + the password-fingerprint-bound JWT session strategy.

Session lifetime ("no unexpected logouts"): the cookie's max-age is 30 days
(config default), and it is SLIDING — api.deps.get_agent_context re-mints +
re-sets the cookie only when the current cookie is near expiry (see
``_slide_session_cookie`` in ``api.deps``), so a stolen cookie's expiry
stays fixed instead of rolling with the legitimate user. Only genuine
inactivity past cloud_auth_cookie_max_age logs out.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import jwt
from fastapi_users import exceptions
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.jwt import decode_jwt, generate_jwt

from roboco.api.auth import revocation
from roboco.config import settings
from roboco.db.tables import UserTable

if TYPE_CHECKING:
    from fastapi_users.manager import BaseUserManager

# Must match panel/src/middleware.ts's SESSION_COOKIE_NAME and
# panel/src/app/(auth)/login/page.tsx's cookie check — cross-language, so
# kept in sync by convention rather than a shared constant.
SESSION_COOKIE_NAME = "roboco_session"

cookie_transport = CookieTransport(
    cookie_name=SESSION_COOKIE_NAME,
    cookie_max_age=settings.cloud_auth_cookie_max_age,
    cookie_secure=True,
    cookie_httponly=True,
    cookie_samesite="lax",
)

_PWD_FINGERPRINT_LEN = 16


def _password_fingerprint(hashed_password: str) -> str:
    """Short, non-reversible fingerprint of the current password hash.

    Bound into every minted token (see ``_SlidingSessionStrategy`` below) so a
    password rotation invalidates every previously-issued cookie — a JWT is
    otherwise stateless and can't be revoked by user id alone.
    """
    return hashlib.sha256(hashed_password.encode("utf-8")).hexdigest()[
        :_PWD_FINGERPRINT_LEN
    ]


def _secret() -> str:
    # Only reached with cloud_auth_enabled (Settings fails loud at startup if
    # the secret is unset in that case); this fallback only matters while the
    # flag is off, when nothing actually mints or reads a token.
    return settings.cloud_auth_secret or "cloud-auth-off"


class _SlidingSessionStrategy(JWTStrategy[UserTable, UUID]):
    """JWTStrategy + password-change invalidation.

    Overrides both token methods to bind an extra ``pwd_fp`` claim to the
    CEO user's current hashed_password — read_token rejects a token whose
    fingerprint no longer matches, so rotating the seeded password (env
    change + restart) invalidates every prior session.
    """

    async def write_token(self, user: UserTable) -> str:
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            "pwd_fp": _password_fingerprint(user.hashed_password),
            "jti": uuid4().hex,
        }
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm
        )

    async def read_token(
        self, token: str | None, user_manager: BaseUserManager[UserTable, UUID]
    ) -> UserTable | None:
        if token is None:
            return None
        try:
            data = decode_jwt(
                token,
                self.decode_key,
                self.token_audience,
                algorithms=[self.algorithm],
            )
            user_id = data.get("sub")
            pwd_fp = data.get("pwd_fp")
            if user_id is None:
                return None
        except jwt.PyJWTError:
            return None

        try:
            parsed_id = user_manager.parse_id(user_id)
            user = await user_manager.get(parsed_id)
        except (exceptions.UserNotExists, exceptions.InvalidID):
            return None

        jti = data.get("jti")
        # pwd_fp short-circuits: the Redis revocation check only runs when
        # the password fingerprint already matches (the common path), so a
        # rotated-password token never touches Redis.
        if pwd_fp != _password_fingerprint(user.hashed_password) or (
            isinstance(jti, str) and await revocation.is_jti_revoked(jti)
        ):
            return None
        return user


def get_jwt_strategy() -> JWTStrategy[UserTable, UUID]:
    return _SlidingSessionStrategy(
        secret=_secret(), lifetime_seconds=settings.cloud_auth_cookie_max_age
    )


auth_backend = AuthenticationBackend[UserTable, UUID](
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)
