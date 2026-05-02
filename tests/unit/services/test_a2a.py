"""Unit tests for A2A conversation_id guards.

Covers the service-layer nil-UUID guard: ``send_chat_message`` must
reject the nil UUID before touching the DB so callers cannot generate
``/conversations//messages`` traffic from a placeholder ID.

The MCP-layer URL-builder test that used to live here was tied to
``roboco.mcp.a2a_server._handle_send_chat_message`` — that module was
deleted as part of the gateway cutover, so the test was dropped along
with it. Agent A2A traffic now flows through ``mcp__roboco-do__*`` and
is exercised by the gateway-tool integration tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from roboco.services.a2a import A2AService

NIL_UUID = UUID("00000000-0000-0000-0000-000000000000")


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
