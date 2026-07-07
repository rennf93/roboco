"""Per-IP rate limit on the cloud-auth login endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from roboco.config import settings

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp


class LoginRateLimiter(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        prefix: str,
        max_attempts: int = 10,
        window: int = 60,
    ) -> None:
        super().__init__(app)
        self.prefix = prefix.rstrip("/")
        self.max_attempts = max_attempts
        self.window = window

    def _client_ip(self, request: Request) -> str:
        # nginx is the single entry point; read the downstream client IP from
        # X-Forwarded-For (first hop) / X-Real-IP, falling back to the peer.
        return (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or request.headers.get("x-real-ip")
            or (request.client.host if request.client else "unknown")
        )

    async def _bump(self, key: str, conn: redis.Redis) -> int:
        count = await conn.incr(key)
        if count == 1:
            await conn.expire(key, self.window)
        return count

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Only the login endpoint is limited; everything else passes through.
        if request.method != "POST" or request.url.path != f"{self.prefix}/login":
            return await call_next(request)
        key = f"auth:login:rl:{self._client_ip(request)}"
        try:
            # Test seam: a fake injected via app.state.login_redis. Production
            # leaves it unset and opens its own per-request connection.
            injected = getattr(request.app.state, "login_redis", None)
            if injected is not None:
                count = await self._bump(key, injected)
            else:
                async with redis.from_url(settings.redis_url) as conn:
                    count = await self._bump(key, conn)
        except Exception:
            # Redis down: fail open. Login is already password-gated; a hard
            # 500 on a redis outage is worse than a relaxed limit.
            return await call_next(request)
        if count > self.max_attempts:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many login attempts. Try again later."},
            )
        return await call_next(request)
