"""api.schemas.websocket coverage."""

from __future__ import annotations

from uuid import uuid4

from roboco.api.schemas.websocket import (
    NewMessageBroadcast,
    WSAgentStream,
    WSMessage,
    WSMessageDelete,
    WSMessageEdit,
    WSMessageNew,
    WSNotification,
    WSSessionClosed,
)


def test_new_message_broadcast() -> None:
    bcast = NewMessageBroadcast(
        channel_id=uuid4(),
        session_id=uuid4(),
        message_id=uuid4(),
        agent_id=uuid4(),
        content="hello",
        message_type="dialogue",
    )
    assert bcast.content == "hello"


def test_ws_message_base() -> None:
    msg = WSMessage(type="custom")
    assert msg.type == "custom"


def test_ws_message_new() -> None:
    msg = WSMessageNew(
        message_id=uuid4(),
        agent_id=uuid4(),
        content="hi",
        message_type="dialogue",
    )
    assert msg.type == "message.new"


def test_ws_message_edit() -> None:
    msg = WSMessageEdit(message_id=uuid4(), content="edited")
    assert msg.type == "message.edit"


def test_ws_message_delete() -> None:
    msg = WSMessageDelete(message_id=uuid4())
    assert msg.type == "message.delete"


def test_ws_agent_stream() -> None:
    msg = WSAgentStream(agent_id=uuid4(), chunk="thinking...")
    assert msg.type == "agent.stream"


def test_ws_session_closed() -> None:
    msg = WSSessionClosed(session_id=uuid4(), reason="timeout")
    assert msg.type == "session.closed"


def test_ws_notification() -> None:
    msg = WSNotification(
        notification_id=uuid4(),
        notification_type="MENTION",
        subject="hi",
        priority="normal",
    )
    assert msg.type == "notification"
