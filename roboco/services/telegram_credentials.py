"""Telegram bot credentials — a Fernet-encrypted singleton row.

Mirrors ``services/x_credentials.py``: the bot token + chat id are indivisible
(a token alone can't target a DM), so this service treats them as
all-or-nothing — set both together, or clear both together. Decryption is
server-side only; ``telegram_client`` is the sole reader of ``get_decrypted``;
the API never returns plaintext (``has_credentials`` boolean pattern).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select

from roboco.db.tables import TelegramCredentialsTable
from roboco.services.base import BaseService
from roboco.utils.crypto import EncryptionError, decrypt_token, encrypt_token

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class TelegramCredentialsValidationError(ValueError):
    """Raised when a partial (not all-or-nothing) credential set is given."""


@dataclass(frozen=True)
class TelegramCredentialsData:
    """The decrypted bot token + chat id, server-side only."""

    bot_token: str
    chat_id: str


class TelegramCredentialsService(BaseService):
    """CRUD for the single ``telegram_credentials`` row."""

    service_name: ClassVar[str] = "telegram_credentials"

    async def _get_row(self) -> TelegramCredentialsTable | None:
        result = await self.session.execute(select(TelegramCredentialsTable).limit(1))
        return result.scalar_one_or_none()

    async def has_credentials(self) -> bool:
        """True iff both the bot token and chat id are stored."""
        row = await self._get_row()
        if row is None:
            return False
        return bool(row.bot_token_encrypted and row.chat_id_encrypted)

    async def set_credentials(self, *, bot_token: str, chat_id: str) -> bool:
        """Set (encrypt) or clear the bot token + chat id together.

        Both empty -> clears the row. Both non-empty -> encrypts and upserts.
        A mixed set (one empty, one not) raises
        :class:`TelegramCredentialsValidationError` — a partial set can't send.
        Returns the resulting ``has_credentials``.
        """
        values = (bot_token, chat_id)
        non_empty = sum(1 for v in values if v)
        if non_empty not in (0, len(values)):
            raise TelegramCredentialsValidationError(
                "bot token and chat id must be set or cleared together"
            )

        row = await self._get_row()
        if non_empty == 0:
            if row is not None:
                await self.session.delete(row)
                await self.session.flush()
            self.log.info("Telegram credentials cleared")
            return False

        try:
            encrypted = (encrypt_token(bot_token), encrypt_token(chat_id))
        except EncryptionError as e:
            self.log.error("Failed to encrypt Telegram credentials", error=str(e))
            raise

        if row is None:
            row = TelegramCredentialsTable()
            self.session.add(row)
        row.bot_token_encrypted, row.chat_id_encrypted = encrypted
        await self.session.flush()
        self.log.info("Telegram credentials set")
        return True

    async def get_decrypted(self) -> TelegramCredentialsData | None:
        """The decrypted bot token + chat id, or None when unset. Server-side only."""
        row = await self._get_row()
        if row is None or not (row.bot_token_encrypted and row.chat_id_encrypted):
            return None
        try:
            return TelegramCredentialsData(
                bot_token=decrypt_token(row.bot_token_encrypted),
                chat_id=decrypt_token(row.chat_id_encrypted),
            )
        except EncryptionError as e:
            self.log.error("Failed to decrypt Telegram credentials", error=str(e))
            raise


def get_telegram_credentials_service(
    session: AsyncSession,
) -> TelegramCredentialsService:
    """Construct a TelegramCredentialsService bound to ``session``."""
    return TelegramCredentialsService(session)
