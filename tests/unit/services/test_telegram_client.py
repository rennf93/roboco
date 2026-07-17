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


_REPLY_TO_MESSAGE_ID = 42
_SENT_MESSAGE_ID = 7


@pytest.mark.asyncio
async def test_live_client_send_message_with_reply_markup_and_message_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["reply_markup"] == {"force_reply": True}
        assert body["reply_to_message_id"] == _REPLY_TO_MESSAGE_ID
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": _SENT_MESSAGE_ID}}
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveTelegramClient(_CREDS, timeout=5.0, client=http_client)
    result = await client.send_message(
        "hi",
        reply_markup={"force_reply": True},
        reply_to_message_id=_REPLY_TO_MESSAGE_ID,
    )
    assert result.sent is True
    assert result.message_id == _SENT_MESSAGE_ID
    await client.close()


@pytest.mark.asyncio
async def test_live_client_get_updates_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bot123456:ABC/getUpdates"
        assert request.url.params["timeout"] == "25"
        assert request.url.params["offset"] == "10"
        return httpx.Response(
            200, json={"ok": True, "result": [{"update_id": 10}, {"update_id": 11}]}
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveTelegramClient(_CREDS, timeout=5.0, client=http_client)
    updates = await client.get_updates(offset=10, timeout=25, limit=50)
    assert [u["update_id"] for u in updates] == [10, 11]
    await client.close()


@pytest.mark.asyncio
async def test_live_client_get_updates_network_error_returns_empty() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveTelegramClient(_CREDS, timeout=5.0, client=http_client)
    updates = await client.get_updates(offset=None, timeout=25, limit=50)
    assert updates == []
    await client.close()


@pytest.mark.asyncio
async def test_live_client_answer_callback_query_posts_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bot123456:ABC/answerCallbackQuery"
        body = json.loads(request.content.decode())
        assert body == {"callback_query_id": "cq1", "text": "ok"}
        return httpx.Response(200, json={"ok": True, "result": True})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveTelegramClient(_CREDS, timeout=5.0, client=http_client)
    await client.answer_callback_query("cq1", "ok")
    await client.close()


@pytest.mark.asyncio
async def test_live_client_edit_message_reply_markup_clears_keyboard() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bot123456:ABC/editMessageReplyMarkup"
        body = json.loads(request.content.decode())
        assert body["reply_markup"] == {}
        return httpx.Response(200, json={"ok": True, "result": True})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveTelegramClient(_CREDS, timeout=5.0, client=http_client)
    await client.edit_message_reply_markup(7, None)
    await client.close()


@pytest.mark.asyncio
async def test_null_client_v2_methods_are_all_noops() -> None:
    client: NullTelegramClient = NullTelegramClient()
    assert await client.get_updates(offset=None, timeout=25, limit=50) == []
    # None of these raise — that's the whole contract.
    await client.answer_callback_query("cq1", "text")
    await client.edit_message_reply_markup(1, None)
    await client.edit_message_text(1, "done")
