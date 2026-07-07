"""Cloud-auth gate coverage smoke.

Arms ``cloud_auth_enabled`` against the REAL in-process API (the shared
``e2e_stack`` fixture) and asserts every Phase 1b auth gate holds through
the live uvicorn + middleware + dependency stack — no stubbed deps:

(a) forged ``X-Agent-Role: ceo`` + ``X-Agent-ID`` + NO token + NO cookie
    → ``GET /api/orchestrator/status`` 401 (the ``_require_ceo`` dual path).
(b) forged ``X-Agent-Role: developer`` + NO token → ``POST
    /api/v1/flow/developer/give_me_work`` 401 (the v1 flow role guard via
    ``_check_agent_auth_token``).
(c) NO credential → ``GET /api/settings`` 401 (``require_panel_token``).
(d) a REAL CEO session cookie (minted via the auth backend's JWT strategy
    over a seeded CEO user row) → ``GET /api/settings`` 200.

The running uvicorn app reads the same ``roboco.config.settings`` singleton
per-request, so monkeypatching it live takes effect without a restart.
"""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    import pytest
    from roboco.db.tables import UserTable
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack


# A fixed HMAC secret so agent-token verification is deterministic; the JWT
# session secret is set on `settings.cloud_auth_secret` separately.
_HMAC_SECRET = "e2e-auth-gate-secret"
_JWT_SECRET = "e2e-jwt-session-secret"
_CEO_EMAIL = "ceo@roboco.local"
_CEO_PASSWORD = "e2e-ceo-password"


def _arm_cloud_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flip the cloud-auth flag + secrets on for the live uvicorn app."""
    from roboco.config import settings

    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setattr(settings, "cloud_auth_secret", _JWT_SECRET)
    monkeypatch.setattr(settings, "cloud_auth_email", _CEO_EMAIL)
    monkeypatch.setattr(settings, "cloud_auth_password", _CEO_PASSWORD)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _HMAC_SECRET)


async def _seed_ceo_user(session: AsyncSession) -> UserTable:
    """Insert the single CEO login row the auth backend resolves cookies against."""
    from fastapi_users.password import PasswordHelper
    from roboco.db.tables import UserTable

    user = UserTable(
        email=_CEO_EMAIL,
        hashed_password=PasswordHelper().hash(_CEO_PASSWORD),
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


def _mint_ceo_session_cookie(user: UserTable) -> str:
    """Mint a real JWT session cookie via the auth backend's strategy."""
    from roboco.api.auth.backend import get_jwt_strategy

    return asyncio.run(get_jwt_strategy().write_token(user))


def test_forged_ceo_header_rejected(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm_cloud_auth(monkeypatch)
    resp = httpx.get(
        f"{e2e_stack.base_url}/api/orchestrator/status",
        headers={
            "X-Agent-ID": _ceo_agent_id(),
            "X-Agent-Role": "ceo",
        },
        timeout=10,
    )
    assert resp.status_code == HTTPStatus.UNAUTHORIZED, (
        f"forged ceo header: expected 401, got {resp.status_code} {resp.text[:300]}"
    )


def test_forged_developer_header_rejected(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm_cloud_auth(monkeypatch)
    resp = httpx.post(
        f"{e2e_stack.base_url}/api/v1/flow/developer/give_me_work",
        headers={
            "X-Agent-ID": "00000000-0000-0000-0000-000000000001",
            "X-Agent-Role": "developer",
        },
        json={},
        timeout=10,
    )
    assert resp.status_code == HTTPStatus.UNAUTHORIZED, (
        f"forged developer header: expected 401, got "
        f"{resp.status_code} {resp.text[:300]}"
    )


def test_settings_no_credential_rejected(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm_cloud_auth(monkeypatch)
    resp = httpx.get(f"{e2e_stack.base_url}/api/settings", timeout=10)
    assert resp.status_code == HTTPStatus.UNAUTHORIZED, (
        f"settings no-credential: expected 401, got "
        f"{resp.status_code} {resp.text[:300]}"
    )


def test_settings_real_ceo_cookie_passes(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm_cloud_auth(monkeypatch)
    user: UserTable = e2e_stack.run_db(_seed_ceo_user)
    cookie = _mint_ceo_session_cookie(user)
    resp = httpx.get(
        f"{e2e_stack.base_url}/api/settings",
        cookies={"roboco_session": cookie},
        timeout=10,
    )
    assert resp.status_code == HTTPStatus.OK, (
        f"settings real cookie: expected 200, got {resp.status_code} {resp.text[:300]}"
    )


def _ceo_agent_id() -> str:
    from roboco.agents_config import CEO_AGENT_ID

    return CEO_AGENT_ID
