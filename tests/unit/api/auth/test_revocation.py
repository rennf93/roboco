"""Redis jti revocation (M36) — round-trip + fail-open on Redis down."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.api.auth import revocation

_REVOKE_TTL = 60


@pytest.mark.asyncio
async def test_revoke_then_check_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    fake.set = AsyncMock()
    fake.exists = AsyncMock(return_value=1)
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(revocation.redis, "from_url", lambda _url: fake)

    await revocation.revoke_jti("abc", _REVOKE_TTL)
    assert await revocation.is_jti_revoked("abc") is True
    # The revoked key carries the namespace prefix.
    args, kwargs = fake.set.call_args
    assert args[0].startswith("roboco:jwt_revoked:")
    assert kwargs.get("ex") == _REVOKE_TTL


@pytest.mark.asyncio
async def test_is_jti_revoked_failopen_on_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = MagicMock()
    fake.exists = AsyncMock(side_effect=ConnectionError("redis down"))
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(revocation.redis, "from_url", lambda _url: fake)

    # Fail-open: a Redis error => not revoked (pwd_fp remains the strong check).
    assert await revocation.is_jti_revoked("abc") is False


@pytest.mark.asyncio
async def test_revoke_jti_noop_on_zero_ttl() -> None:
    # No Redis interaction when ttl <= 0.
    await revocation.revoke_jti("abc", 0)  # must not raise
