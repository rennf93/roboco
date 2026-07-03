"""XCredentialsService coverage — encrypt/roundtrip, all-or-nothing set/clear.

Drives a real ``db_session`` via the project's Postgres-backed conftest. The
service never returns plaintext to a caller other than ``get_decrypted`` (the
server-side-only reader) — the API layer only ever sees ``has_credentials``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from roboco.db.tables import XCredentialsTable
from roboco.services.x_credentials import (
    XCredentialsService,
    XCredentialsValidationError,
    get_x_credentials_service,
)
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_CREDS = {
    "api_key": "ak-test",
    "api_secret": "as-test",
    "access_token": "at-test",
    "access_token_secret": "ats-test",
}


@pytest_asyncio.fixture
async def svc(db_session: AsyncSession) -> AsyncIterator[XCredentialsService]:
    yield get_x_credentials_service(db_session)


@pytest.mark.asyncio
async def test_unset_has_no_credentials(svc: XCredentialsService) -> None:
    assert await svc.has_credentials() is False
    assert await svc.get_decrypted() is None


@pytest.mark.asyncio
async def test_set_all_four_encrypts_and_roundtrips(svc: XCredentialsService) -> None:
    has_creds = await svc.set_credentials(**_CREDS)
    assert has_creds is True
    assert await svc.has_credentials() is True

    decrypted = await svc.get_decrypted()
    assert decrypted is not None
    assert decrypted.api_key == _CREDS["api_key"]
    assert decrypted.api_secret == _CREDS["api_secret"]
    assert decrypted.access_token == _CREDS["access_token"]
    assert decrypted.access_token_secret == _CREDS["access_token_secret"]


@pytest.mark.asyncio
async def test_stored_row_never_holds_plaintext(
    svc: XCredentialsService, db_session: AsyncSession
) -> None:
    await svc.set_credentials(**_CREDS)
    result = await db_session.execute(select(XCredentialsTable).limit(1))
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.api_key_encrypted != _CREDS["api_key"]
    assert row.api_secret_encrypted != _CREDS["api_secret"]
    assert row.access_token_encrypted != _CREDS["access_token"]
    assert row.access_token_secret_encrypted != _CREDS["access_token_secret"]


@pytest.mark.asyncio
async def test_clearing_all_four_removes_row(svc: XCredentialsService) -> None:
    await svc.set_credentials(**_CREDS)
    has_creds = await svc.set_credentials(
        api_key="", api_secret="", access_token="", access_token_secret=""
    )
    assert has_creds is False
    assert await svc.has_credentials() is False
    assert await svc.get_decrypted() is None


@pytest.mark.asyncio
async def test_partial_set_is_rejected(svc: XCredentialsService) -> None:
    with pytest.raises(XCredentialsValidationError):
        await svc.set_credentials(
            api_key="only-one", api_secret="", access_token="", access_token_secret=""
        )


@pytest.mark.asyncio
async def test_rotate_overwrites_previous_values(svc: XCredentialsService) -> None:
    await svc.set_credentials(**_CREDS)
    rotated = {k: f"{v}-rotated" for k, v in _CREDS.items()}
    await svc.set_credentials(**rotated)
    decrypted = await svc.get_decrypted()
    assert decrypted is not None
    assert decrypted.api_key == rotated["api_key"]
