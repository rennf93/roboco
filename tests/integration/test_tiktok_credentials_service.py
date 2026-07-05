"""TikTokCredentialsService coverage — encrypt/roundtrip, all-or-nothing
set/clear, and the update_tokens refresh-rotation write.

Mirrors test_x_credentials_service.py. The service never returns plaintext
to a caller other than `get_decrypted` (the server-side-only reader) — the
API layer only ever sees `has_credentials`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from roboco.db.tables import TikTokCredentialsTable
from roboco.services.tiktok_credentials import (
    TikTokCredentialsService,
    TikTokCredentialsValidationError,
    get_tiktok_credentials_service,
)
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_CREDS = {
    "client_key": "ck-test",
    "client_secret": "cs-test",
    "access_token": "at-test",
    "refresh_token": "rt-test",
}


@pytest_asyncio.fixture
async def svc(db_session: AsyncSession) -> AsyncIterator[TikTokCredentialsService]:
    yield get_tiktok_credentials_service(db_session)


@pytest.mark.asyncio
async def test_unset_has_no_credentials(svc: TikTokCredentialsService) -> None:
    assert await svc.has_credentials() is False
    assert await svc.get_decrypted() is None


@pytest.mark.asyncio
async def test_set_all_four_encrypts_and_roundtrips(
    svc: TikTokCredentialsService,
) -> None:
    has_creds = await svc.set_credentials(**_CREDS)
    assert has_creds is True
    assert await svc.has_credentials() is True

    decrypted = await svc.get_decrypted()
    assert decrypted is not None
    assert decrypted.client_key == _CREDS["client_key"]
    assert decrypted.client_secret == _CREDS["client_secret"]
    assert decrypted.access_token == _CREDS["access_token"]
    assert decrypted.refresh_token == _CREDS["refresh_token"]


@pytest.mark.asyncio
async def test_stored_row_never_holds_plaintext(
    svc: TikTokCredentialsService, db_session: AsyncSession
) -> None:
    await svc.set_credentials(**_CREDS)
    result = await db_session.execute(select(TikTokCredentialsTable).limit(1))
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.client_key_encrypted != _CREDS["client_key"]
    assert row.client_secret_encrypted != _CREDS["client_secret"]
    assert row.access_token_encrypted != _CREDS["access_token"]
    assert row.refresh_token_encrypted != _CREDS["refresh_token"]


@pytest.mark.asyncio
async def test_clearing_all_four_removes_row(svc: TikTokCredentialsService) -> None:
    await svc.set_credentials(**_CREDS)
    has_creds = await svc.set_credentials(
        client_key="", client_secret="", access_token="", refresh_token=""
    )
    assert has_creds is False
    assert await svc.has_credentials() is False
    assert await svc.get_decrypted() is None


@pytest.mark.asyncio
async def test_partial_set_is_rejected(svc: TikTokCredentialsService) -> None:
    with pytest.raises(TikTokCredentialsValidationError):
        await svc.set_credentials(
            client_key="only-one", client_secret="", access_token="", refresh_token=""
        )


@pytest.mark.asyncio
async def test_rotate_overwrites_previous_values(svc: TikTokCredentialsService) -> None:
    await svc.set_credentials(**_CREDS)
    rotated = {k: f"{v}-rotated" for k, v in _CREDS.items()}
    await svc.set_credentials(**rotated)
    decrypted = await svc.get_decrypted()
    assert decrypted is not None
    assert decrypted.client_key == rotated["client_key"]


@pytest.mark.asyncio
async def test_update_tokens_rotates_access_and_refresh_only(
    svc: TikTokCredentialsService,
) -> None:
    await svc.set_credentials(**_CREDS)
    await svc.update_tokens(access_token="at-new", refresh_token="rt-new")

    decrypted = await svc.get_decrypted()
    assert decrypted is not None
    assert decrypted.access_token == "at-new"
    assert decrypted.refresh_token == "rt-new"
    # client_key/client_secret are untouched by the narrower refresh write.
    assert decrypted.client_key == _CREDS["client_key"]
    assert decrypted.client_secret == _CREDS["client_secret"]


@pytest.mark.asyncio
async def test_update_tokens_before_any_credentials_set_raises(
    svc: TikTokCredentialsService,
) -> None:
    with pytest.raises(TikTokCredentialsValidationError):
        await svc.update_tokens(access_token="at-new", refresh_token="rt-new")
