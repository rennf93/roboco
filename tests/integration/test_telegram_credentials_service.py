"""TelegramCredentialsService coverage — encrypt/roundtrip, all-or-nothing set/clear.

Drives a real ``db_session`` via the project's Postgres-backed conftest. The
service never returns plaintext to a caller other than ``get_decrypted`` (the
server-side-only reader) — the API layer only ever sees ``has_credentials``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from roboco.db.tables import TelegramCredentialsTable
from roboco.services.telegram_credentials import (
    TelegramCredentialsService,
    TelegramCredentialsValidationError,
    get_telegram_credentials_service,
)
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_CREDS = {"bot_token": "123456:ABC-bot-token", "chat_id": "987654321"}


@pytest_asyncio.fixture
async def svc(
    db_session: AsyncSession,
) -> AsyncIterator[TelegramCredentialsService]:
    yield get_telegram_credentials_service(db_session)


@pytest.mark.asyncio
async def test_unset_has_no_credentials(svc: TelegramCredentialsService) -> None:
    assert await svc.has_credentials() is False
    assert await svc.get_decrypted() is None


@pytest.mark.asyncio
async def test_set_both_encrypts_and_roundtrips(
    svc: TelegramCredentialsService,
) -> None:
    has_creds = await svc.set_credentials(**_CREDS)
    assert has_creds is True
    assert await svc.has_credentials() is True

    decrypted = await svc.get_decrypted()
    assert decrypted is not None
    assert decrypted.bot_token == _CREDS["bot_token"]
    assert decrypted.chat_id == _CREDS["chat_id"]


@pytest.mark.asyncio
async def test_stored_row_never_holds_plaintext(
    svc: TelegramCredentialsService, db_session: AsyncSession
) -> None:
    await svc.set_credentials(**_CREDS)
    result = await db_session.execute(select(TelegramCredentialsTable).limit(1))
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.bot_token_encrypted != _CREDS["bot_token"]
    assert row.chat_id_encrypted != _CREDS["chat_id"]


@pytest.mark.asyncio
async def test_clearing_both_removes_row(svc: TelegramCredentialsService) -> None:
    await svc.set_credentials(**_CREDS)
    has_creds = await svc.set_credentials(bot_token="", chat_id="")
    assert has_creds is False
    assert await svc.has_credentials() is False
    assert await svc.get_decrypted() is None


@pytest.mark.asyncio
async def test_partial_set_is_rejected(svc: TelegramCredentialsService) -> None:
    with pytest.raises(TelegramCredentialsValidationError):
        await svc.set_credentials(bot_token="only-one", chat_id="")


@pytest.mark.asyncio
async def test_rotate_overwrites_previous_values(
    svc: TelegramCredentialsService,
) -> None:
    await svc.set_credentials(**_CREDS)
    rotated = {k: f"{v}-rotated" for k, v in _CREDS.items()}
    await svc.set_credentials(**rotated)
    decrypted = await svc.get_decrypted()
    assert decrypted is not None
    assert decrypted.bot_token == rotated["bot_token"]
