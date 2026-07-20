"""GitHub App credentials — a singleton row (mirrors ``telegram_credentials.py``).

Unlike the Telegram/X pattern, only the private key is a secret worth
Fernet-encrypting; ``app_id`` is a public identifier (visible on the App's own
GitHub settings page, comparable to an OAuth client id) so it is stored plain.
Both fields are still treated all-or-nothing: an App id without its key can't
sign a JWT, and vice versa. Decryption is server-side only — ``get_decrypted``
is read by ``github_app_auth`` when minting installation tokens; the API never
returns the key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select

from roboco.db.tables import GitHubAppCredentialsTable
from roboco.services.base import BaseService
from roboco.utils.crypto import EncryptionError, decrypt_token, encrypt_token

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class GitHubAppCredentialsValidationError(ValueError):
    """Raised when a partial (not all-or-nothing) credential set is given."""


@dataclass(frozen=True)
class GitHubAppCredentialsData:
    """The App id + decrypted private key, server-side only."""

    app_id: str
    private_key: str


class GitHubAppCredentialsService(BaseService):
    """CRUD for the single ``github_app_credentials`` row."""

    service_name: ClassVar[str] = "github_app_credentials"

    async def _get_row(self) -> GitHubAppCredentialsTable | None:
        result = await self.session.execute(select(GitHubAppCredentialsTable).limit(1))
        return result.scalar_one_or_none()

    async def has_credentials(self) -> bool:
        """True iff both the App id and private key are stored."""
        row = await self._get_row()
        if row is None:
            return False
        return bool(row.app_id and row.private_key_encrypted)

    async def set_credentials(self, *, app_id: str, private_key: str) -> bool:
        """Set or clear the App id + private key together.

        Both empty -> clears the row. Both non-empty -> stores (encrypting the
        key). A mixed set (one empty, one not) raises
        :class:`GitHubAppCredentialsValidationError` — a partial set can't sign.
        Returns the resulting ``has_credentials``.
        """
        values = (app_id, private_key)
        non_empty = sum(1 for v in values if v)
        if non_empty not in (0, len(values)):
            raise GitHubAppCredentialsValidationError(
                "app_id and private_key must be set or cleared together"
            )

        row = await self._get_row()
        if non_empty == 0:
            if row is not None:
                await self.session.delete(row)
                await self.session.flush()
            self.log.info("GitHub App credentials cleared")
            return False

        try:
            encrypted_key = encrypt_token(private_key)
        except EncryptionError as e:
            self.log.error("Failed to encrypt GitHub App private key", error=str(e))
            raise

        if row is None:
            row = GitHubAppCredentialsTable()
            self.session.add(row)
        row.app_id = app_id
        row.private_key_encrypted = encrypted_key
        await self.session.flush()
        self.log.info("GitHub App credentials set")
        return True

    async def get_decrypted(self) -> GitHubAppCredentialsData | None:
        """The App id + decrypted private key, or None when unset."""
        row = await self._get_row()
        if row is None or not (row.app_id and row.private_key_encrypted):
            return None
        try:
            return GitHubAppCredentialsData(
                app_id=row.app_id,
                private_key=decrypt_token(row.private_key_encrypted),
            )
        except EncryptionError as e:
            self.log.error("Failed to decrypt GitHub App private key", error=str(e))
            raise


def get_github_app_credentials_service(
    session: AsyncSession,
) -> GitHubAppCredentialsService:
    """Construct a GitHubAppCredentialsService bound to ``session``."""
    return GitHubAppCredentialsService(session)
