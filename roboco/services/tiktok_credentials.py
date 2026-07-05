"""TikTok OAuth2 credentials — a Fernet-encrypted singleton row.

Mirrors ``XCredentialsService``'s all-or-nothing posture for the initial
set/clear of the four secrets, but also exposes ``update_tokens`` — the
OAuth2 refresh grant rotates access_token (and often refresh_token) without
touching client_key/client_secret, so that path needs its own narrower write
that doesn't require the full four-value set. Decryption is server-side
only — ``tiktok_client`` is the sole reader of ``get_decrypted``; the API
never returns plaintext (``has_credentials`` boolean pattern).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select

from roboco.db.tables import TikTokCredentialsTable
from roboco.services.base import BaseService
from roboco.utils.crypto import EncryptionError, decrypt_token, encrypt_token

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class TikTokCredentialsValidationError(ValueError):
    """Raised on a partial (not all-or-nothing) credential set, or an
    ``update_tokens`` call before any credentials are stored."""


@dataclass(frozen=True)
class TikTokCredentialsData:
    """The four decrypted OAuth2 secrets, server-side only."""

    client_key: str
    client_secret: str
    access_token: str
    refresh_token: str


class TikTokCredentialsService(BaseService):
    """CRUD for the single ``tiktok_credentials`` row."""

    service_name: ClassVar[str] = "tiktok_credentials"

    async def _get_row(self) -> TikTokCredentialsTable | None:
        result = await self.session.execute(select(TikTokCredentialsTable).limit(1))
        return result.scalar_one_or_none()

    async def has_credentials(self) -> bool:
        """True iff all four secrets are stored."""
        row = await self._get_row()
        if row is None:
            return False
        return bool(
            row.client_key_encrypted
            and row.client_secret_encrypted
            and row.access_token_encrypted
            and row.refresh_token_encrypted
        )

    async def set_credentials(
        self,
        *,
        client_key: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
    ) -> bool:
        """Set (encrypt) or clear the four secrets together.

        All four empty -> clears the row. All four non-empty -> encrypts and
        upserts. A mixed set raises :class:`TikTokCredentialsValidationError` —
        a partial secret set can't authenticate a request. Returns the
        resulting ``has_credentials``.
        """
        values = (client_key, client_secret, access_token, refresh_token)
        non_empty = sum(1 for v in values if v)
        if non_empty not in (0, len(values)):
            raise TikTokCredentialsValidationError(
                "all four TikTok credentials must be set or cleared together"
            )

        row = await self._get_row()
        if non_empty == 0:
            if row is not None:
                await self.session.delete(row)
                await self.session.flush()
            self.log.info("TikTok credentials cleared")
            return False

        try:
            encrypted = (
                encrypt_token(client_key),
                encrypt_token(client_secret),
                encrypt_token(access_token),
                encrypt_token(refresh_token),
            )
        except EncryptionError as e:
            self.log.error("Failed to encrypt TikTok credentials", error=str(e))
            raise

        if row is None:
            row = TikTokCredentialsTable()
            self.session.add(row)
        (
            row.client_key_encrypted,
            row.client_secret_encrypted,
            row.access_token_encrypted,
            row.refresh_token_encrypted,
        ) = encrypted
        await self.session.flush()
        self.log.info("TikTok credentials set")
        return True

    async def update_tokens(self, *, access_token: str, refresh_token: str) -> None:
        """Persist a rotated access/refresh token pair from the OAuth2
        refresh grant. Requires an existing row (``set_credentials`` must
        have run first) and leaves client_key/client_secret untouched — the
        narrower write the refresh path needs so it never has to re-supply
        the static app secrets just to rotate a token.
        """
        row = await self._get_row()
        if row is None:
            raise TikTokCredentialsValidationError(
                "cannot update tokens before TikTok credentials are set"
            )
        try:
            row.access_token_encrypted = encrypt_token(access_token)
            row.refresh_token_encrypted = encrypt_token(refresh_token)
        except EncryptionError as e:
            self.log.error("Failed to encrypt refreshed TikTok tokens", error=str(e))
            raise
        await self.session.flush()
        self.log.info("TikTok tokens refreshed")

    async def get_decrypted(self) -> TikTokCredentialsData | None:
        """The four decrypted secrets, or None when unset. Server-side only."""
        row = await self._get_row()
        if row is None or not (
            row.client_key_encrypted
            and row.client_secret_encrypted
            and row.access_token_encrypted
            and row.refresh_token_encrypted
        ):
            return None
        try:
            return TikTokCredentialsData(
                client_key=decrypt_token(row.client_key_encrypted),
                client_secret=decrypt_token(row.client_secret_encrypted),
                access_token=decrypt_token(row.access_token_encrypted),
                refresh_token=decrypt_token(row.refresh_token_encrypted),
            )
        except EncryptionError as e:
            self.log.error("Failed to decrypt TikTok credentials", error=str(e))
            raise


def get_tiktok_credentials_service(session: AsyncSession) -> TikTokCredentialsService:
    """Construct a TikTokCredentialsService bound to ``session``."""
    return TikTokCredentialsService(session)
