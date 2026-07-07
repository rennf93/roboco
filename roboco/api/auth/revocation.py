"""Redis-backed JWT ``jti`` revocation set for cloud-auth logout.

A stolen cloud-auth cookie is otherwise stateless-valid for
``cloud_auth_cookie_max_age``. On logout the current token's ``jti`` is added
here with a TTL matching the cookie's remaining life; ``read_token`` rejects
any token whose ``jti`` is in the set. Fail-OPEN on Redis unavailable — the
``pwd_fp`` password-rotation check (``backend._SlidingSessionStrategy``)
remains the strong user-wide revocation; ``jti`` is the per-session logout
kill, and a Redis hiccup must not lock out the CEO. Mirrors
``login_limit``'s per-request ``async with redis.from_url(settings.redis_url)``
shape.
"""

from __future__ import annotations

import logging

import redis.asyncio as redis

from roboco.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "roboco:jwt_revoked:"


async def revoke_jti(jti: str, ttl_seconds: int) -> None:
    """Mark ``jti`` as revoked for ``ttl_seconds`` (≈ the cookie's remaining life)."""
    if not jti or ttl_seconds <= 0:
        return
    try:
        async with redis.from_url(settings.redis_url) as conn:
            await conn.set(_KEY_PREFIX + jti, "1", ex=ttl_seconds)
    except Exception:
        logger.warning("jti revocation write failed (fail-open)", exc_info=True)


async def is_jti_revoked(jti: str) -> bool:
    """True iff ``jti`` is in the revocation set. Fail-open (False) on Redis error."""
    if not jti:
        return False
    try:
        async with redis.from_url(settings.redis_url) as conn:
            return bool(await conn.exists(_KEY_PREFIX + jti))
    except Exception:
        logger.warning("jti revocation check failed (fail-open)", exc_info=True)
        return False
