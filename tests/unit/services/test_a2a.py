"""Unit tests for A2A conversation_id guards.

Covers:
- send_chat_message service rejects the nil UUID before touching the DB
- _handle_send_chat_message MCP helper rejects empty/blank conversation_id
  before building the URL (which would produce ``/conversations//messages``)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from roboco.mcp.a2a_server import _handle_send_chat_message
from roboco.services.a2a import A2AService

NIL_UUID = UUID("00000000-0000-0000-0000-000000000000")
REAL_UUID = UUID("12345678-1234-5678-1234-567812345678")


# ---------------------------------------------------------------------------
# Service layer — send_chat_message nil-UUID guard
# ---------------------------------------------------------------------------


class TestSendChatMessageServiceGuard:
    """send_chat_message raises ValueError on nil UUID before any DB access."""

    def _service(self) -> A2AService:
        session = MagicMock()
        # execute should never be called — if it is the test will still pass
        # but we want to be sure no DB round-trip happens on nil UUID
        session.execute = AsyncMock(side_effect=AssertionError("DB must not be hit"))
        return A2AService(session)

    @pytest.mark.asyncio
    async def test_nil_uuid_raises_value_error(self) -> None:
        """Nil UUID (all-zeros) is rejected immediately with a clear message."""
        svc = self._service()
        with pytest.raises(ValueError, match="nil UUID"):
            await svc.send_chat_message(
                conversation_id=NIL_UUID,
                from_agent="be-dev-1",
                content="hello",
            )

    @pytest.mark.asyncio
    async def test_nil_uuid_does_not_hit_database(self) -> None:
        """No DB query is issued when conversation_id is the nil UUID."""
        session = MagicMock()
        executed = False

        async def _fake_execute(*_args: object, **_kwargs: object) -> object:
            nonlocal executed
            executed = True
            return MagicMock()

        session.execute = _fake_execute
        svc = A2AService(session)

        with pytest.raises(ValueError):
            await svc.send_chat_message(
                conversation_id=NIL_UUID,
                from_agent="be-dev-1",
                content="hello",
            )

        assert not executed, "Database must not be queried for nil conversation_id"


# ---------------------------------------------------------------------------
# MCP layer — _handle_send_chat_message URL-builder guard
# ---------------------------------------------------------------------------


class TestHandleSendChatMessageUrlGuard:
    """_handle_send_chat_message returns an error dict — no URL is built — when
    conversation_id is empty or whitespace-only."""

    @pytest.mark.asyncio
    async def test_empty_string_returns_error(self) -> None:
        """Empty string produces a structured error, not a 404 from //messages.

        format_error_response wraps as {"error": {"code": ..., "message": ...}}.
        """
        result = await _handle_send_chat_message(
            agent_id="be-dev-1",
            conversation_id="",
            message="hello",
        )
        assert "error" in result
        assert result["error"]["code"] == "INVALID_CONVERSATION_ID"

    @pytest.mark.asyncio
    async def test_whitespace_string_returns_error(self) -> None:
        """Whitespace-only string is treated as empty."""
        result = await _handle_send_chat_message(
            agent_id="be-dev-1",
            conversation_id="   ",
            message="hello",
        )
        assert "error" in result
        assert result["error"]["code"] == "INVALID_CONVERSATION_ID"

    @pytest.mark.asyncio
    async def test_error_message_mentions_start(self) -> None:
        """Error hint guides agent to call roboco_a2a_start() first."""
        result = await _handle_send_chat_message(
            agent_id="be-dev-1",
            conversation_id="",
            message="hello",
        )
        assert "roboco_a2a_start" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_valid_uuid_string_proceeds_to_http(self) -> None:
        """A real (non-empty) conversation_id is NOT short-circuited by the guard.

        We don't mock httpx here — the function will attempt a real connection
        and raise ConnectError. That proves the guard was passed and the URL
        was attempted (httpx.ConnectError → format_error_response with
        API_UNAVAILABLE, not INVALID_CONVERSATION_ID).
        """
        result = await _handle_send_chat_message(
            agent_id="be-dev-1",
            conversation_id=str(REAL_UUID),
            message="hello",
        )
        # The request hit the network path (no real server → API_UNAVAILABLE)
        # rather than being short-circuited by the empty-ID guard.
        error_block = result.get("error") or {}
        assert error_block.get("code") != "INVALID_CONVERSATION_ID"
