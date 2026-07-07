"""Cloud auth dual-path coverage (ROBOCO_CLOUD_AUTH_ENABLED, default off).

Proves the survival guarantee — off mode is byte-for-byte today's
header-trust behavior — and the on-mode enforcement: header-trust dies for
the CEO/human identity, the agent HMAC path (incl. the orchestrator's
`system` self-PATCH) is untouched in both modes, a valid session cookie
authenticates as CEO, the cookie slides on every authenticated request, and
the seeded user is upserted idempotently.
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import jwt as _jwt
import pytest
from fastapi import HTTPException, Response
from fastapi_users.password import PasswordHelper
from roboco.agents_config import CEO_AGENT_ID, issue_agent_token
from roboco.api import websocket as ws_module
from roboco.api.auth import revocation
from roboco.api.auth.backend import SESSION_COOKIE_NAME, get_jwt_strategy
from roboco.api.auth.routes import auth_status
from roboco.api.auth.seed import ensure_seed_user
from roboco.api.deps import _slide_session_cookie, get_agent_context
from roboco.config import settings
from roboco.db.tables import UserTable
from roboco.models import AgentRole
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

_HTTP_401 = 401
_SECRET = "test-secret-for-cloud-auth-padded-32b"
_SYSTEM_AGENT_ID = "00000000-0000-0000-0000-000000000000"
_password_helper = PasswordHelper()


@pytest.fixture(autouse=True)
def _cloud_auth_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this module gets a signing secret, flag on or off."""
    monkeypatch.setattr(settings, "cloud_auth_secret", _SECRET)


# ---------------------------------------------------------------------------
# OFF mode — byte-for-byte header-trust, even for a "ceo" role header.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_mode_ceo_header_spoof_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The survival guarantee: flag off, a bare X-Agent-Role: ceo header
    (no token, no session) authenticates exactly like it does today."""
    monkeypatch.setattr(settings, "cloud_auth_enabled", False)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "false")
    aid = UUID(CEO_AGENT_ID)
    with patch(
        "roboco.api.deps.resolve_agent_identity",
        new=AsyncMock(return_value=(aid, "ceo")),
    ):
        ctx = await get_agent_context(
            MagicMock(),
            MagicMock(),
            x_agent_id=CEO_AGENT_ID,
            x_agent_role="ceo",
        )
    assert ctx.agent_id == aid
    assert ctx.role == AgentRole.CEO


@pytest.mark.asyncio
async def test_off_mode_developer_header_trust_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", False)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "false")
    aid = uuid4()
    with patch(
        "roboco.api.deps.resolve_agent_identity",
        new=AsyncMock(return_value=(aid, "be-dev-1")),
    ):
        ctx = await get_agent_context(
            MagicMock(),
            MagicMock(),
            x_agent_id="be-dev-1",
            x_agent_role="developer",
            x_agent_team="backend",
        )
    assert ctx.agent_id == aid
    assert ctx.role == AgentRole.DEVELOPER


# ---------------------------------------------------------------------------
# ON mode — header-trust kill for the CEO identity.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_mode_spoofed_ceo_header_without_token_or_session_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    with pytest.raises(HTTPException) as exc:
        await get_agent_context(
            MagicMock(),
            MagicMock(),
            x_agent_id=CEO_AGENT_ID,
            x_agent_role="ceo",
        )
    assert exc.value.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_on_mode_no_headers_no_cookie_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No X-Agent-* headers at all also falls into the CEO/cookie path and
    401s without a valid session — never silently falls back to anything."""
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    with pytest.raises(HTTPException) as exc:
        await get_agent_context(MagicMock(), MagicMock())
    assert exc.value.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_on_mode_valid_session_yields_ceo_context_and_slides_cookie(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    # Fresh cookie is inside the remint window so the sliding mechanism
    # issues a Set-Cookie (M36: re-mint only near expiry, not every request).
    monkeypatch.setattr(settings, "cloud_auth_remint_threshold_seconds", 2592001)
    user = UserTable(
        email="ceo@example.com",
        hashed_password=_password_helper.hash("hunter2"),
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    db_session.add(user)
    await db_session.flush()

    token = await get_jwt_strategy().write_token(user)
    response = Response()

    ctx = await get_agent_context(
        db_session,
        response,
        roboco_session=token,
    )

    assert ctx.role == AgentRole.CEO
    assert ctx.agent_id == UUID(CEO_AGENT_ID)
    # The sliding-session mechanism: a fresh Set-Cookie is re-issued.
    set_cookie = response.headers.get("set-cookie")
    assert set_cookie is not None
    assert SESSION_COOKIE_NAME in set_cookie


@pytest.mark.asyncio
async def test_on_mode_invalid_session_cookie_401(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    with pytest.raises(HTTPException) as exc:
        await get_agent_context(db_session, Response(), roboco_session="not-a-real-jwt")
    assert exc.value.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_on_mode_agent_hmac_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent-fleet HMAC path is untouched by the cloud-auth flag."""
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    aid = uuid4()
    token = issue_agent_token(str(aid), "developer", "backend")
    with patch(
        "roboco.api.deps.resolve_agent_identity",
        new=AsyncMock(return_value=(aid, "be-dev-1")),
    ):
        ctx = await get_agent_context(
            MagicMock(),
            MagicMock(),
            x_agent_id=str(aid),
            x_agent_role="developer",
            x_agent_team="backend",
            x_agent_token=token,
        )
    assert ctx.agent_id == aid
    assert ctx.role == AgentRole.DEVELOPER


@pytest.mark.asyncio
async def test_on_mode_system_self_patch_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The orchestrator's own `system` self-PATCH identity is untouched."""
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    token = issue_agent_token(_SYSTEM_AGENT_ID, "system", "")
    ctx = await get_agent_context(
        MagicMock(),
        MagicMock(),
        x_agent_id=_SYSTEM_AGENT_ID,
        x_agent_role="system",
        x_agent_token=token,
    )
    assert ctx.agent_id == UUID(_SYSTEM_AGENT_ID)
    assert ctx.slug == "system"


@pytest.mark.asyncio
async def test_on_mode_forged_agent_token_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    with pytest.raises(HTTPException) as exc:
        await get_agent_context(
            MagicMock(),
            MagicMock(),
            x_agent_id="be-dev-1",
            x_agent_role="developer",
            x_agent_token="forged",
        )
    assert exc.value.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_on_mode_spoofed_non_ceo_role_without_token_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Header-trust is dead in cloud-auth mode for EVERY privileged role, not
    just ceo: a bare X-Agent-Role: main_pm with no token is a spoof and 401s
    (this closes the LAN hole for PM/board roles too, independent of
    ROBOCO_AGENT_AUTH_REQUIRED)."""
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    with pytest.raises(HTTPException) as exc:
        await get_agent_context(
            MagicMock(),
            MagicMock(),
            x_agent_id="main-pm",
            x_agent_role="main_pm",
        )
    assert exc.value.status_code == _HTTP_401


# ---------------------------------------------------------------------------
# /api/auth/status — always public, shape-checked both ways.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_status_reports_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", False)
    assert await auth_status() == {"cloud_auth_enabled": False}


@pytest.mark.asyncio
async def test_auth_status_reports_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    assert await auth_status() == {"cloud_auth_enabled": True}


# ---------------------------------------------------------------------------
# Seeded-user idempotent upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_seed_user_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", False)
    monkeypatch.setattr(settings, "cloud_auth_email", "ceo@example.com")
    monkeypatch.setattr(settings, "cloud_auth_password", "hunter2")
    await ensure_seed_user(db_session)
    await db_session.flush()
    rows = (await db_session.execute(select(UserTable))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_ensure_seed_user_creates_exactly_one_row(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setattr(settings, "cloud_auth_email", "ceo@example.com")
    monkeypatch.setattr(settings, "cloud_auth_password", "hunter2")

    await ensure_seed_user(db_session)
    await db_session.flush()
    rows = (await db_session.execute(select(UserTable))).scalars().all()
    assert len(rows) == 1
    assert rows[0].email == "ceo@example.com"
    assert rows[0].hashed_password != "hunter2"


@pytest.mark.asyncio
async def test_ensure_seed_user_idempotent_same_password(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setattr(settings, "cloud_auth_email", "ceo@example.com")
    monkeypatch.setattr(settings, "cloud_auth_password", "hunter2")

    await ensure_seed_user(db_session)
    await db_session.flush()
    first_hash = (
        (await db_session.execute(select(UserTable))).scalar_one().hashed_password
    )

    await ensure_seed_user(db_session)
    await db_session.flush()
    rows = (await db_session.execute(select(UserTable))).scalars().all()
    assert len(rows) == 1
    assert rows[0].hashed_password == first_hash


@pytest.mark.asyncio
async def test_ensure_seed_user_rotates_password_and_invalidates_hash(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setattr(settings, "cloud_auth_email", "ceo@example.com")
    monkeypatch.setattr(settings, "cloud_auth_password", "hunter2")
    await ensure_seed_user(db_session)
    await db_session.flush()
    first_hash = (
        (await db_session.execute(select(UserTable))).scalar_one().hashed_password
    )

    monkeypatch.setattr(settings, "cloud_auth_password", "a-new-password")
    await ensure_seed_user(db_session)
    await db_session.flush()
    rows = (await db_session.execute(select(UserTable))).scalars().all()
    assert len(rows) == 1
    assert rows[0].hashed_password != first_hash


@pytest.mark.asyncio
async def test_ensure_seed_user_email_change_renames_the_single_row(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setattr(settings, "cloud_auth_email", "ceo@example.com")
    monkeypatch.setattr(settings, "cloud_auth_password", "hunter2")
    await ensure_seed_user(db_session)
    await db_session.flush()

    monkeypatch.setattr(settings, "cloud_auth_email", "renzo@example.com")
    await ensure_seed_user(db_session)
    await db_session.flush()

    rows = (await db_session.execute(select(UserTable))).scalars().all()
    assert len(rows) == 1
    assert rows[0].email == "renzo@example.com"


@pytest.mark.asyncio
async def test_ensure_seed_user_warns_without_email_or_password(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setattr(settings, "cloud_auth_email", None)
    monkeypatch.setattr(settings, "cloud_auth_password", None)
    await ensure_seed_user(db_session)
    await db_session.flush()
    rows = (await db_session.execute(select(UserTable))).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# WS panel-token gate — dual path
# ---------------------------------------------------------------------------


def _mock_ws(
    headers: dict[str, str] | None = None, cookies: dict[str, str] | None = None
) -> MagicMock:
    ws = MagicMock()
    ws.headers = headers or {}
    ws.cookies = cookies or {}
    return ws


@pytest.mark.asyncio
async def test_ws_off_mode_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", False)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    ws = _mock_ws()
    assert await ws_module._require_panel_token(ws) is True


@pytest.mark.asyncio
async def test_ws_on_mode_valid_hmac_token_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    token = issue_agent_token(CEO_AGENT_ID, "ceo", "")
    ws = _mock_ws(headers={"x-agent-token": token})
    assert await ws_module._require_panel_token(ws) is True


@pytest.mark.asyncio
async def test_ws_on_mode_forged_hmac_token_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    ws = _mock_ws(headers={"x-agent-token": "forged"})
    assert await ws_module._require_panel_token(ws) is False


@pytest.mark.asyncio
async def test_ws_on_mode_valid_session_cookie_accepted(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)

    async def _stub_get_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    monkeypatch.setattr(ws_module, "get_db", _stub_get_db)

    user = UserTable(
        email="ceo@example.com",
        hashed_password=_password_helper.hash("hunter2"),
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    token = await get_jwt_strategy().write_token(user)

    ws = _mock_ws(cookies={SESSION_COOKIE_NAME: token})
    assert await ws_module._require_panel_token(ws) is True


@pytest.mark.asyncio
async def test_ws_on_mode_no_token_no_cookie_rejected(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)

    async def _stub_get_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    monkeypatch.setattr(ws_module, "get_db", _stub_get_db)
    ws = _mock_ws()
    assert await ws_module._require_panel_token(ws) is False


# ---------------------------------------------------------------------------
# M36 — JWT jti claim + sliding cookie re-mints only near expiry.
# ---------------------------------------------------------------------------

_MIN_JTI_LEN = 16  # uuid4().hex is 32 chars; floor guards against truncation


def _make_user() -> UserTable:
    return UserTable(
        email="ceo@roboco.test",
        hashed_password=_password_helper.hash("x"),
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )


@pytest.mark.asyncio
async def test_write_token_carries_jti(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    user = _make_user()
    token = await get_jwt_strategy().write_token(user)
    data = _jwt.decode(
        token, _SECRET, algorithms=["HS256"], audience="fastapi-users:auth"
    )
    assert isinstance(data.get("jti"), str) and len(data["jti"]) >= _MIN_JTI_LEN


@pytest.mark.asyncio
async def test_slide_does_not_remint_when_far_from_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cookie with plenty of life left is NOT re-minted — so a stolen
    cookie's exp stays fixed instead of rolling with the legit user."""
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setattr(settings, "cloud_auth_cookie_max_age", 2592000)
    monkeypatch.setattr(settings, "cloud_auth_remint_threshold_seconds", 86400)

    user = _make_user()
    fresh = await get_jwt_strategy().write_token(user)
    response = Response()
    await _slide_session_cookie(response, user, fresh)
    assert "set-cookie" not in {k.lower() for k in response.headers}


@pytest.mark.asyncio
async def test_slide_remints_when_near_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    monkeypatch.setattr(settings, "cloud_auth_cookie_max_age", 2592000)
    monkeypatch.setattr(settings, "cloud_auth_remint_threshold_seconds", 86400)

    user = _make_user()
    near = await get_jwt_strategy().write_token(user)
    data = _jwt.decode(
        near, _SECRET, algorithms=["HS256"], audience="fastapi-users:auth"
    )
    data["exp"] = int(_time.time()) + 3600
    near_expired = _jwt.encode(data, _SECRET, algorithm="HS256")
    response = Response()
    await _slide_session_cookie(response, user, near_expired)
    assert "set-cookie" in {k.lower() for k in response.headers}


# ---------------------------------------------------------------------------
# M36b — Redis jti revocation: read_token rejects a revoked jti.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_token_rejects_revoked_jti(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    user = UserTable(
        id=uuid4(),
        email="ceo@roboco.test",
        hashed_password=_password_helper.hash("pw"),
    )
    token = await get_jwt_strategy().write_token(user)
    monkeypatch.setattr(revocation, "is_jti_revoked", AsyncMock(return_value=True))

    manager = MagicMock()
    manager.parse_id.return_value = user.id
    manager.get = AsyncMock(return_value=user)
    assert await get_jwt_strategy().read_token(token, manager) is None


@pytest.mark.asyncio
async def test_read_token_accepts_unrevoked_jti(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cloud_auth_enabled", True)
    user = UserTable(
        id=uuid4(),
        email="ceo@roboco.test",
        hashed_password=_password_helper.hash("pw"),
    )
    token = await get_jwt_strategy().write_token(user)
    monkeypatch.setattr(revocation, "is_jti_revoked", AsyncMock(return_value=False))

    manager = MagicMock()
    manager.parse_id.return_value = user.id
    manager.get = AsyncMock(return_value=user)
    result = await get_jwt_strategy().read_token(token, manager)
    assert result is not None and str(result.id) == str(user.id)
