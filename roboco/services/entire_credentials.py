"""The Entire API token for the trails bridge — a Fernet-encrypted singleton.

Mirrors ``services/telegram_credentials.py`` with a single secret. The token
is decrypted server-side only, by the trails bridge, straight into the
Entire CLI subprocess's environment (``ENTIRE_TOKEN``): never argv, never a
file, never an agent container. The API never returns it
(``has_token`` boolean pattern).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select

from roboco.db.tables import EntireCredentialsTable
from roboco.services.base import BaseService
from roboco.utils.crypto import EncryptionError, decrypt_token, encrypt_token

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class EntireCredentialsService(BaseService):
    """CRUD for the single ``entire_credentials`` row."""

    service_name: ClassVar[str] = "entire_credentials"

    async def _get_row(self) -> EntireCredentialsTable | None:
        result = await self.session.execute(select(EntireCredentialsTable).limit(1))
        return result.scalar_one_or_none()

    async def has_token(self) -> bool:
        """True iff a token is stored."""
        row = await self._get_row()
        return row is not None and bool(row.token_encrypted)

    async def set_token(self, token: str) -> bool:
        """Set (encrypt) the token, or clear it with an empty string.
        Returns the resulting ``has_token``."""
        row = await self._get_row()
        if not token:
            if row is not None:
                await self.session.delete(row)
                await self.session.flush()
            self.log.info("Entire token cleared")
            return False
        try:
            encrypted = encrypt_token(token)
        except EncryptionError as e:
            self.log.error("Failed to encrypt the Entire token", error=str(e))
            raise
        if row is None:
            row = EntireCredentialsTable()
            self.session.add(row)
        row.token_encrypted = encrypted
        await self.session.flush()
        self.log.info("Entire token set")
        return True

    async def get_decrypted(self) -> str | None:
        """The decrypted token, or None when unset. Server-side only."""
        row = await self._get_row()
        if row is None or not row.token_encrypted:
            return None
        try:
            return decrypt_token(row.token_encrypted)
        except EncryptionError as e:
            self.log.error("Failed to decrypt the Entire token", error=str(e))
            raise


def get_entire_credentials_service(session: AsyncSession) -> EntireCredentialsService:
    """Construct an EntireCredentialsService bound to ``session``."""
    return EntireCredentialsService(session)
