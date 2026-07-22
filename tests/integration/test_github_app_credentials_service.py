"""GitHubAppCredentialsService coverage — plain app_id, encrypted private key,
all-or-nothing set/clear (mirrors ``test_telegram_credentials_service.py``).

Drives a real ``db_session`` via the project's Postgres-backed conftest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from roboco.db.tables import GitHubAppCredentialsTable
from roboco.services.github_app_credentials import (
    GitHubAppCredentialsService,
    GitHubAppCredentialsValidationError,
    get_github_app_credentials_service,
)
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _generate_rsa_pem() -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


_CREDS = {
    "app_id": "123456",
    "private_key": _generate_rsa_pem(),
}


@pytest_asyncio.fixture
async def svc(
    db_session: AsyncSession,
) -> AsyncIterator[GitHubAppCredentialsService]:
    yield get_github_app_credentials_service(db_session)


@pytest.mark.asyncio
async def test_unset_has_no_credentials(svc: GitHubAppCredentialsService) -> None:
    assert await svc.has_credentials() is False
    assert await svc.get_decrypted() is None


@pytest.mark.asyncio
async def test_set_both_stores_and_roundtrips(
    svc: GitHubAppCredentialsService,
) -> None:
    has_creds = await svc.set_credentials(**_CREDS)
    assert has_creds is True
    assert await svc.has_credentials() is True

    decrypted = await svc.get_decrypted()
    assert decrypted is not None
    assert decrypted.app_id == _CREDS["app_id"]
    assert decrypted.private_key == _CREDS["private_key"]


@pytest.mark.asyncio
async def test_app_id_stored_plain_key_encrypted(
    svc: GitHubAppCredentialsService, db_session: AsyncSession
) -> None:
    await svc.set_credentials(**_CREDS)
    result = await db_session.execute(select(GitHubAppCredentialsTable).limit(1))
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.app_id == _CREDS["app_id"]
    assert row.private_key_encrypted != _CREDS["private_key"]


@pytest.mark.asyncio
async def test_clearing_both_removes_row(svc: GitHubAppCredentialsService) -> None:
    await svc.set_credentials(**_CREDS)
    has_creds = await svc.set_credentials(app_id="", private_key="")
    assert has_creds is False
    assert await svc.has_credentials() is False
    assert await svc.get_decrypted() is None


@pytest.mark.asyncio
async def test_partial_set_is_rejected(svc: GitHubAppCredentialsService) -> None:
    with pytest.raises(GitHubAppCredentialsValidationError):
        await svc.set_credentials(app_id="only-one", private_key="")


@pytest.mark.asyncio
async def test_rotate_overwrites_previous_values(
    svc: GitHubAppCredentialsService,
) -> None:
    await svc.set_credentials(**_CREDS)
    rotated = {"app_id": "654321", "private_key": _generate_rsa_pem()}
    await svc.set_credentials(**rotated)
    decrypted = await svc.get_decrypted()
    assert decrypted is not None
    assert decrypted.app_id == rotated["app_id"]
    assert decrypted.private_key == rotated["private_key"]


@pytest.mark.asyncio
async def test_malformed_pem_is_rejected(svc: GitHubAppCredentialsService) -> None:
    with pytest.raises(GitHubAppCredentialsValidationError):
        await svc.set_credentials(app_id="123456", private_key="not-a-pem-at-all")
    assert await svc.has_credentials() is False
