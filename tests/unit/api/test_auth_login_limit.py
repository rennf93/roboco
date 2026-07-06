from __future__ import annotations

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from roboco.api.auth.login_limit import LoginRateLimiter


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    async def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        self.ttl[key] = ttl
        return True


def _app(redis):
    app = FastAPI()
    app.state.login_redis = redis
    app.add_middleware(LoginRateLimiter, prefix="/auth", max_attempts=3, window=60)

    @app.post("/auth/login")
    async def login():
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_login_rate_limited_after_max():
    redis = _FakeRedis()
    client = TestClient(_app(redis))
    for _ in range(3):
        assert client.post("/auth/login").status_code == status.HTTP_200_OK
    assert client.post("/auth/login").status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_non_login_path_unaffected():
    redis = _FakeRedis()
    app = _app(redis)

    @app.get("/auth/status")
    async def auth_status():
        return {"cloud_auth_enabled": True}

    client = TestClient(app)
    assert client.get("/auth/status").status_code == status.HTTP_200_OK
