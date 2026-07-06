"""Settings routes (/api/settings) are gated behind the panel token.

Under cloud_auth the panel reaches these routes with a CEO session cookie; the
gate (``require_panel_token``) accepts the cookie OR a valid CEO HMAC token.
In dev mode a missing token is a no-op (existing behavior).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.agents_config import issue_panel_token
from roboco.api import deps as _deps
from roboco.api.routes.settings import router as settings_router
from roboco.db.base import get_db

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SECRET = "test-secret-for-settings-routes"
_HTTP_200 = 200
_HTTP_401 = 401


@pytest_asyncio.fixture
async def settings_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    fake_service = MagicMock()
    fake_service.all = AsyncMock(return_value={})
    monkeypatch.setattr(
        "roboco.api.routes.settings.get_settings_service",
        lambda _db: fake_service,
    )

    async def _fake_db() -> AsyncIterator[object]:
        yield object()

    app = FastAPI()
    app.include_router(settings_router, prefix="/api/settings")
    app.dependency_overrides[get_db] = _fake_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_settings_rejects_no_credential_under_cloud_auth(
    settings_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = await settings_client.get("/api/settings")
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_settings_accepts_valid_token_under_cloud_auth(
    settings_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    r = await settings_client.get(
        "/api/settings", headers={"X-Agent-Token": issue_panel_token()}
    )
    assert r.status_code == _HTTP_200


@pytest.mark.asyncio
async def test_settings_accepts_valid_cookie_under_cloud_auth(
    settings_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roboco.api.auth.backend import SESSION_COOKIE_NAME

    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    with patch(
        "roboco.api.deps.resolve_session_user",
        new=AsyncMock(return_value=MagicMock()),
    ):
        r = await settings_client.get(
            "/api/settings", cookies={SESSION_COOKIE_NAME: "valid"}
        )
    assert r.status_code == _HTTP_200


@pytest.mark.asyncio
async def test_settings_dev_mode_no_token_passes(
    settings_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", False)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = await settings_client.get("/api/settings")
    assert r.status_code == _HTTP_200


@pytest.mark.asyncio
async def test_settings_put_rejects_no_credential_under_cloud_auth(
    settings_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = await settings_client.put("/api/settings/some.key", json={"value": "x"})
    assert r.status_code == _HTTP_401
