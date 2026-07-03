"""X (Twitter) OAuth 1.0a credentials — a Fernet-encrypted singleton row.

Mirrors the Fernet encryption + tri-state posture of `services/provider.py`,
but the four OAuth 1.0a user-context secrets are indivisible (a partial set
can't sign a request), so this service treats them as all-or-nothing: set all
four together, or clear all four together. Decryption is server-side only —
`x_client` is the sole reader of `get_decrypted`; the API never returns
plaintext (`has_credentials` boolean pattern, matching `has_git_token`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select

from roboco.db.tables import XCredentialsTable
from roboco.services.base import BaseService
from roboco.utils.crypto import EncryptionError, decrypt_token, encrypt_token

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class XCredentialsValidationError(ValueError):
    """Raised when a partial (not all-or-nothing) credential set is given."""


@dataclass(frozen=True)
class XCredentialsData:
    """The four decrypted OAuth 1.0a user-context secrets, server-side only."""

    api_key: str
    api_secret: str
    access_token: str
    access_token_secret: str


class XCredentialsService(BaseService):
    """CRUD for the single `x_credentials` row."""

    service_name: ClassVar[str] = "x_credentials"

    async def _get_row(self) -> XCredentialsTable | None:
        result = await self.session.execute(select(XCredentialsTable).limit(1))
        return result.scalar_one_or_none()

    async def has_credentials(self) -> bool:
        """True iff all four secrets are stored."""
        row = await self._get_row()
        if row is None:
            return False
        return bool(
            row.api_key_encrypted
            and row.api_secret_encrypted
            and row.access_token_encrypted
            and row.access_token_secret_encrypted
        )

    async def set_credentials(
        self,
        *,
        api_key: str,
        api_secret: str,
        access_token: str,
        access_token_secret: str,
    ) -> bool:
        """Set (encrypt) or clear the four secrets together.

        All four empty -> clears the row. All four non-empty -> encrypts and
        upserts. A mixed set (some empty, some not) raises
        :class:`XCredentialsValidationError` — a partial OAuth 1.0a secret set
        cannot sign a request, so there is no meaningful "leave unchanged for
        this one" tri-state here. Returns the resulting `has_credentials`.
        """
        values = (api_key, api_secret, access_token, access_token_secret)
        non_empty = sum(1 for v in values if v)
        if non_empty not in (0, len(values)):
            raise XCredentialsValidationError(
                "all four X credentials must be set or cleared together"
            )

        row = await self._get_row()
        if non_empty == 0:
            if row is not None:
                await self.session.delete(row)
                await self.session.flush()
            self.log.info("X credentials cleared")
            return False

        try:
            encrypted = (
                encrypt_token(api_key),
                encrypt_token(api_secret),
                encrypt_token(access_token),
                encrypt_token(access_token_secret),
            )
        except EncryptionError as e:
            self.log.error("Failed to encrypt X credentials", error=str(e))
            raise

        if row is None:
            row = XCredentialsTable()
            self.session.add(row)
        (
            row.api_key_encrypted,
            row.api_secret_encrypted,
            row.access_token_encrypted,
            row.access_token_secret_encrypted,
        ) = encrypted
        await self.session.flush()
        self.log.info("X credentials set")
        return True

    async def get_decrypted(self) -> XCredentialsData | None:
        """The four decrypted secrets, or None when unset. Server-side only."""
        row = await self._get_row()
        if row is None or not (
            row.api_key_encrypted
            and row.api_secret_encrypted
            and row.access_token_encrypted
            and row.access_token_secret_encrypted
        ):
            return None
        try:
            return XCredentialsData(
                api_key=decrypt_token(row.api_key_encrypted),
                api_secret=decrypt_token(row.api_secret_encrypted),
                access_token=decrypt_token(row.access_token_encrypted),
                access_token_secret=decrypt_token(row.access_token_secret_encrypted),
            )
        except EncryptionError as e:
            self.log.error("Failed to decrypt X credentials", error=str(e))
            raise


def get_x_credentials_service(session: AsyncSession) -> XCredentialsService:
    """Construct an XCredentialsService bound to `session`."""
    return XCredentialsService(session)
