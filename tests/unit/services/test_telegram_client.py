"""TelegramClient coverage: NullTelegramClient no-op, LiveTelegramClient calls."""

from __future__ import annotations

import json

import httpx
import pytest
from roboco.services.telegram_client import (
    LiveTelegramClient,
    NullTelegramClient,
    build_telegram_client,
)
from roboco.services.telegram_credentials import TelegramCredentialsData

_CREDS = TelegramCredentialsData(bot_token="123456:ABC", chat_id="987654321")


def test_null_client_is_unconfigured() -> None:
    client = build_telegram_client(None, timeout=5.0)
    assert isinstance(client, NullTelegramClient)
    assert client.configured is False


@pytest.mark.asyncio
async def test_null_client_send_message_is_a_noop() -> None:
    client: NullTelegramClient = NullTelegramClient()
    result = await client.send_message("hello")
    assert result.sent is False
    assert result.detail  # non-empty reason


def test_build_telegram_client_with_creds_returns_live_client() -> None:
    client = build_telegram_client(_CREDS, timeout=5.0)
    assert isinstance(client, LiveTelegramClient)
    assert client.configured is True


@pytest.mark.asyncio
async def test_live_client_send_message_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bot123456:ABC/sendMessage"
        body = json.loads(request.content.decode())
        assert body == {"chat_id": _CREDS.chat_id, "text": "hi"}
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveTelegramClient(_CREDS, timeout=5.0, client=http_client)
    result = await client.send_message("hi")
    assert result.sent is True
    await client.close()


@pytest.mark.asyncio
async def test_live_client_send_message_http_error_is_graceful() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveTelegramClient(_CREDS, timeout=5.0, client=http_client)
    result = await client.send_message("hi")
    assert result.sent is False
    assert "401" in result.detail
    await client.close()
