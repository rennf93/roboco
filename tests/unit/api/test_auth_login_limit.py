from __future__ import annotations

from typing import Any

from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from roboco.api.auth.login_limit import LoginRateLimiter


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.ttl[key] = ttl
        return True


class _BrokenRedis:
    """Simulates a redis outage: every incr raises."""

    async def incr(self, _key: str) -> int:
        raise RuntimeError("redis down")

    async def expire(self, _key: str, _ttl: int) -> bool:
        raise RuntimeError("redis down")


def _app(redis: Any) -> FastAPI:
    app = FastAPI()
    app.state.login_redis = redis
    app.add_middleware(LoginRateLimiter, prefix="/auth", max_attempts=3, window=60)

    @app.post("/auth/login")
    async def login() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_login_rate_limited_after_max() -> None:
    redis = _FakeRedis()
    client = TestClient(_app(redis))
    for _ in range(3):
        assert client.post("/auth/login").status_code == status.HTTP_200_OK
    assert client.post("/auth/login").status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_non_login_path_unaffected() -> None:
    redis = _FakeRedis()
    app = _app(redis)

    @app.get("/auth/status")
    async def auth_status() -> dict[str, bool]:
        return {"cloud_auth_enabled": True}

    client = TestClient(app)
    assert client.get("/auth/status").status_code == status.HTTP_200_OK


def test_login_keys_off_x_forwarded_for_first_hop() -> None:
    # nginx fronts the app and sets X-Forwarded-For; the limiter must key off
    # the first hop (the real downstream client), not the nginx peer IP.
    redis = _FakeRedis()
    client = TestClient(_app(redis))

    # Burn through the limit from one XFF IP.
    for _ in range(3):
        resp = client.post(
            "/auth/login", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
        )
        assert resp.status_code == status.HTTP_200_OK
    # 4th from the same XFF IP is over the limit.
    assert (
        client.post(
            "/auth/login", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
        ).status_code
        == status.HTTP_429_TOO_MANY_REQUESTS
    )
    # A different XFF IP is a separate bucket and stays under the limit.
    assert (
        client.post(
            "/auth/login", headers={"X-Forwarded-For": "198.51.100.42"}
        ).status_code
        == status.HTTP_200_OK
    )
    # Only the first hop matters; a different second hop with the same first
    # hop is still the same bucket.
    assert (
        client.post(
            "/auth/login", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.2"}
        ).status_code
        == status.HTTP_429_TOO_MANY_REQUESTS
    )


def test_login_fails_open_when_redis_down() -> None:
    # Redis outage must not hard-500 the login endpoint; login is password-gated
    # so a relaxed limit is the safer failure mode.
    client = TestClient(_app(_BrokenRedis()))
    for _ in range(5):
        assert client.post("/auth/login").status_code == status.HTTP_200_OK
