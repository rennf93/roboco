"""api.schemas.websocket coverage."""

from __future__ import annotations

from uuid import uuid4

from roboco.api.schemas.websocket import (
    WSAgentStream,
    WSMessage,
    WSNotification,
)


def test_ws_message_base() -> None:
    msg = WSMessage(type="custom")
    assert msg.type == "custom"


def test_ws_agent_stream() -> None:
    msg = WSAgentStream(agent_id=uuid4(), chunk="thinking...")
    assert msg.type == "agent.stream"


def test_ws_notification() -> None:
    msg = WSNotification(
        notification_id=uuid4(),
        notification_type="MENTION",
        subject="hi",
        priority="normal",
    )
    assert msg.type == "notification"
