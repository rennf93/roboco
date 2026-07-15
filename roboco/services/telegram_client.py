"""Telegram Bot API client — server-side only, agents never touch it.

Thin httpx wrapper for the one operation the V1 bridge needs: send a message
to a chat. Mirrors ``services/x_client.py``'s ``NullXClient`` shape — a
``NullTelegramClient`` is returned when credentials are unset, so the
notification fan-out degrades gracefully (no-op, no exception) exactly like an
unconfigured X client — never raises into the caller, never makes a network
call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from roboco.services.telegram_credentials import TelegramCredentialsData

_API_BASE = "https://api.telegram.org"


@dataclass(frozen=True)
class TelegramSendResult:
    """Outcome of a ``send_message`` call."""

    sent: bool
    detail: str = ""


class TelegramClient(ABC):
    """Abstract Telegram API surface. ``NullTelegramClient`` is the
    graceful-degradation stub."""

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def send_message(self, text: str) -> TelegramSendResult: ...


class NullTelegramClient(TelegramClient):
    """No credentials configured — every call is a no-op, never raises."""

    @property
    def configured(self) -> bool:
        return False

    async def send_message(self, text: str) -> TelegramSendResult:
        _ = text
        return TelegramSendResult(sent=False, detail="no credentials configured")


class LiveTelegramClient(TelegramClient):
    """Real Bot API ``sendMessage`` call."""

    def __init__(
        self,
        creds: TelegramCredentialsData,
        *,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._creds = creds
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return True

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send_message(self, text: str) -> TelegramSendResult:
        url = f"{_API_BASE}/bot{self._creds.bot_token}/sendMessage"
        client = await self._http()
        try:
            resp = await client.post(
                url,
                json={"chat_id": self._creds.chat_id, "text": text},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            return TelegramSendResult(sent=False, detail=f"network error: {exc}")
        if not resp.is_success:
            return TelegramSendResult(
                sent=False,
                detail=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return TelegramSendResult(sent=True)


def build_telegram_client(
    creds: TelegramCredentialsData | None,
    *,
    timeout: float,
    client: httpx.AsyncClient | None = None,
) -> TelegramClient:
    """Construct the client — ``NullTelegramClient`` when credentials are unset."""
    if creds is None:
        return NullTelegramClient()
    return LiveTelegramClient(creds, timeout=timeout, client=client)
