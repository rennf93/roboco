"""Telegram Mini App sign-in route coverage.

Covers the conditional mount (both `telegram_miniapp_enabled` AND
`cloud_auth_enabled` required) and the exchange flow: valid signed initData
+ matching CEO chat id -> the same cloud-auth session cookie `/api/auth/login`
issues; every refusal path (unconfigured creds, bad HMAC, wrong user id,
oversized body) never mints a cookie.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi_users.password import PasswordHelper
from httpx import ASGITransport, AsyncClient
from roboco.api.app import create_app
from roboco.api.auth.backend import SESSION_COOKIE_NAME
from roboco.api.deps import get_db
from roboco.api.routes.telegram import webapp_auth_router
from roboco.config import settings
from roboco.db.tables import UserTable
from roboco.services.telegram_credentials import get_telegram_credentials_service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_BOT_TOKEN = "123456:TEST-bot-token"
_CHAT_ID = "987654321"
_SECRET = "test-secret-for-miniapp-padded-32bytes"
_password_helper = PasswordHelper()


def _sign(fields: dict[str, str], bot_token: str = _BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id: str = _CHAT_ID, bot_token: str = _BOT_TOKEN) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": int(user_id), "first_name": "Renzo"}),
        "query_id": "AAH_test",
    }
    signed = dict(fields)
    signed["hash"] = _sign(fields, bot_token)
    return urlencode(signed)


@pytest.fixture(autouse=True)
def _armed_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets a signing secret + encryption key + both flags on."""
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setattr(settings, "cloud_auth_secret", _SECRET)
    monkeypatch.setattr(settings, "telegram_miniapp_enabled", True)
    monkeypatch.setattr(settings, "telegram_initdata_max_age_seconds", 600)
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())


async def _seed_creds(db: AsyncSession) -> None:
    await get_telegram_credentials_service(db).set_credentials(
        bot_token=_BOT_TOKEN, chat_id=_CHAT_ID
    )
    await db.flush()


async def _seed_user(db: AsyncSession) -> UserTable:
    user = UserTable(
        email="ceo@example.com",
        hashed_password=_password_helper.hash("hunter2"),
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


def _build_app(db_session: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.include_router(webapp_auth_router, prefix="/api/telegram")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return app


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = _build_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Conditional mount — both flags required. Built through the REAL
# `roboco.api.app.create_app()` factory (the exact pattern used by
# `tests/unit/api/test_app.py` and `tests/unit/runtime/test_rate_limit_sweep.py`)
# rather than a test-local mirror of app.py's inline conditional-mount logic,
# so app.py:560-571 (the actual mount gate) is the code under test.
# ---------------------------------------------------------------------------


def _registered_paths(app: FastAPI) -> set[str]:
    """Every registered path, robust to FastAPI 0.137+ routing internals.

    Mirrors ``tests/unit/api/test_app.py``'s helper of the same name: from
    0.137, ``include_router`` wraps each sub-router in an ``_IncludedRouter``
    (a ``BaseRoute`` with no ``.path``) instead of flattening its routes into
    ``app.routes``, so collect HTTP paths from the OpenAPI schema (the stable
    public contract) plus each included router's prefix.
    """
    paths = set(app.openapi().get("paths", {}))
    for r in app.routes:
        path = getattr(r, "path", None)
        if isinstance(path, str):
            paths.add(path)
        prefix = getattr(getattr(r, "include_context", None), "prefix", None)
        if prefix:
            paths.add(prefix)
    return paths


def test_mount_skipped_when_miniapp_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "telegram_miniapp_enabled", False)
    app = create_app()
    assert "/api/telegram/webapp-auth" not in _registered_paths(app)


def test_mount_skipped_when_cloud_auth_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", False)
    app = create_app()
    assert "/api/telegram/webapp-auth" not in _registered_paths(app)


def test_mount_included_when_both_armed() -> None:
    # `_armed_settings` (autouse) already has both flags on.
    app = create_app()
    assert "/api/telegram/webapp-auth" in _registered_paths(app)
    # Two `LoginRateLimiter` instances are installed (one guards
    # `/api/auth/login`, another this route) — check the union of every
    # instance's configured paths, not just the first match.
    limited_paths: set[str] = set()
    for m in app.user_middleware:
        if getattr(m.cls, "__name__", "") != "LoginRateLimiter":
            continue
        raw_paths = m.kwargs.get("paths")
        if isinstance(raw_paths, tuple | list):
            limited_paths.update(str(p) for p in raw_paths)
    assert "/api/telegram/webapp-auth" in limited_paths


# ---------------------------------------------------------------------------
# Exchange flow.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_sets_session_cookie(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _seed_creds(db_session)
    await _seed_user(db_session)
    resp = await client.post(
        "/api/telegram/webapp-auth", json={"init_data": _init_data()}
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"ok": True}
    assert resp.cookies.get(SESSION_COOKIE_NAME) is not None


@pytest.mark.asyncio
async def test_wrong_user_id_refused(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _seed_creds(db_session)
    await _seed_user(db_session)
    resp = await client.post(
        "/api/telegram/webapp-auth",
        json={"init_data": _init_data(user_id="111111111")},
    )
    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert resp.cookies.get(SESSION_COOKIE_NAME) is None


@pytest.mark.asyncio
async def test_bad_hmac_refused(db_session: AsyncSession, client: AsyncClient) -> None:
    await _seed_creds(db_session)
    await _seed_user(db_session)
    resp = await client.post(
        "/api/telegram/webapp-auth",
        json={"init_data": _init_data(bot_token="a-different-bot-token")},
    )
    assert resp.status_code == HTTPStatus.UNAUTHORIZED
    assert resp.cookies.get(SESSION_COOKIE_NAME) is None


@pytest.mark.asyncio
async def test_unconfigured_credentials_refused(client: AsyncClient) -> None:
    # No _seed_creds() call — credentials never set.
    resp = await client.post(
        "/api/telegram/webapp-auth", json={"init_data": _init_data()}
    )
    assert resp.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert resp.cookies.get(SESSION_COOKIE_NAME) is None


@pytest.mark.asyncio
async def test_oversized_body_refused(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _seed_creds(db_session)
    await _seed_user(db_session)
    resp = await client.post(
        "/api/telegram/webapp-auth", json={"init_data": "x" * 5000}
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert resp.cookies.get(SESSION_COOKIE_NAME) is None
