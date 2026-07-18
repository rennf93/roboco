"""Telegram Bot API client — server-side only, agents never touch it.

Thin httpx wrapper. V1 needed only ``send_message``; V2 (inbound commands +
actionable buttons) adds ``get_updates`` (long-poll), ``answer_callback_query``,
and the two ``edit_message_*`` calls a callback handler uses to clear a
button row and stamp the outcome. Mirrors ``services/x_client.py``'s
``NullXClient`` shape — a ``NullTelegramClient`` is returned when credentials
are unset, so both the notification fan-out and the inbound poll degrade
gracefully (no-op, no exception) — never raises into the caller, never makes
a network call.
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from roboco.services.telegram_credentials import TelegramCredentialsData

logger = structlog.get_logger()

_API_BASE = "https://api.telegram.org"


@dataclass(frozen=True)
class TelegramSendResult:
    """Outcome of a ``send_message`` call. ``message_id`` is the sent
    message's id — callers that need to edit it back later (the actionable
    force_reply flow) or key a pending action off it read this."""

    sent: bool
    detail: str = ""
    message_id: int | None = None


class TelegramClient(ABC):
    """Abstract Telegram API surface. ``NullTelegramClient`` is the
    graceful-degradation stub."""

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def send_message(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        disable_link_preview: bool = False,
    ) -> TelegramSendResult: ...

    @abstractmethod
    async def get_updates(
        self, *, offset: int | None, timeout: int, limit: int
    ) -> list[dict[str, Any]]:
        """Long-poll ``getUpdates``. Best-effort: a network/parse failure
        returns ``[]`` rather than raising (the poll loop just tries again
        next cycle)."""
        ...

    @abstractmethod
    async def answer_callback_query(
        self, callback_query_id: str, text: str = ""
    ) -> None:
        """Acknowledge a callback_query so the button stops spinning."""
        ...

    @abstractmethod
    async def edit_message_reply_markup(
        self, message_id: int, reply_markup: dict[str, Any] | None
    ) -> None: ...

    @abstractmethod
    async def edit_message_text(
        self,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        disable_link_preview: bool = False,
    ) -> None: ...

    @abstractmethod
    async def close(self) -> None:
        """Release transport resources; no-op when no transport exists."""


class NullTelegramClient(TelegramClient):
    """No credentials configured — every call is a no-op, never raises."""

    async def close(self) -> None:
        return None

    @property
    def configured(self) -> bool:
        return False

    async def send_message(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        disable_link_preview: bool = False,
    ) -> TelegramSendResult:
        _ = (text, reply_markup, reply_to_message_id, parse_mode, disable_link_preview)
        return TelegramSendResult(sent=False, detail="no credentials configured")

    async def get_updates(
        self, *, offset: int | None, timeout: int, limit: int
    ) -> list[dict[str, Any]]:
        _ = (offset, timeout, limit)
        return []

    async def answer_callback_query(
        self, callback_query_id: str, text: str = ""
    ) -> None:
        _ = (callback_query_id, text)

    async def edit_message_reply_markup(
        self, message_id: int, reply_markup: dict[str, Any] | None
    ) -> None:
        _ = (message_id, reply_markup)

    async def edit_message_text(
        self,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        disable_link_preview: bool = False,
    ) -> None:
        _ = (message_id, text, parse_mode, disable_link_preview)


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

    @staticmethod
    def _format_payload(
        payload: dict[str, Any],
        *,
        parse_mode: str | None,
        disable_link_preview: bool,
    ) -> None:
        """Mutates ``payload`` in place with the formatting fields shared by
        ``sendMessage``/``editMessageText`` — only when actually set, so a
        plain-text caller's payload is byte-for-byte unchanged. Bot API 7.0+
        field name (``disable_web_page_preview`` is the legacy alias, still
        accepted, but ``link_preview_options`` is current)."""
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if disable_link_preview:
            payload["link_preview_options"] = {"is_disabled": True}

    async def send_message(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        disable_link_preview: bool = False,
    ) -> TelegramSendResult:
        url = f"{_API_BASE}/bot{self._creds.bot_token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": self._creds.chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        self._format_payload(
            payload, parse_mode=parse_mode, disable_link_preview=disable_link_preview
        )
        client = await self._http()
        try:
            resp = await client.post(url, json=payload, timeout=self._timeout)
        except httpx.HTTPError as exc:
            return TelegramSendResult(sent=False, detail=f"network error: {exc}")
        if not resp.is_success:
            return TelegramSendResult(
                sent=False,
                detail=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        result = resp.json().get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return TelegramSendResult(sent=True, message_id=message_id)

    async def get_updates(
        self, *, offset: int | None, timeout: int, limit: int
    ) -> list[dict[str, Any]]:
        url = f"{_API_BASE}/bot{self._creds.bot_token}/getUpdates"
        params: dict[str, Any] = {"timeout": timeout, "limit": limit}
        if offset is not None:
            params["offset"] = offset
        client = await self._http()
        try:
            # A modest margin over the server-side long-poll wait so the HTTP
            # client doesn't time out a call that's legitimately still
            # blocked inside Telegram's own `timeout` window.
            resp = await client.get(url, params=params, timeout=timeout + 5)
        except httpx.HTTPError:
            return []
        if not resp.is_success:
            return []
        data = resp.json()
        result = data.get("result") if isinstance(data, dict) else None
        return result if isinstance(result, list) else []

    async def answer_callback_query(
        self, callback_query_id: str, text: str = ""
    ) -> None:
        url = f"{_API_BASE}/bot{self._creds.bot_token}/answerCallbackQuery"
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        client = await self._http()
        with contextlib.suppress(httpx.HTTPError):
            await client.post(url, json=payload, timeout=self._timeout)

    async def edit_message_reply_markup(
        self, message_id: int, reply_markup: dict[str, Any] | None
    ) -> None:
        url = f"{_API_BASE}/bot{self._creds.bot_token}/editMessageReplyMarkup"
        payload: dict[str, Any] = {
            "chat_id": self._creds.chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup or {},
        }
        client = await self._http()
        with contextlib.suppress(httpx.HTTPError):
            await client.post(url, json=payload, timeout=self._timeout)

    async def edit_message_text(
        self,
        message_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        disable_link_preview: bool = False,
    ) -> None:
        url = f"{_API_BASE}/bot{self._creds.bot_token}/editMessageText"
        payload: dict[str, Any] = {
            "chat_id": self._creds.chat_id,
            "message_id": message_id,
            "text": text,
        }
        self._format_payload(
            payload, parse_mode=parse_mode, disable_link_preview=disable_link_preview
        )
        client = await self._http()
        with contextlib.suppress(httpx.HTTPError):
            resp = await client.post(url, json=payload, timeout=self._timeout)
            if not resp.is_success:
                logger.warning(
                    "telegram edit_message_text failed",
                    status_code=resp.status_code,
                    detail=resp.text[:200],
                )


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
